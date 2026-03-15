import json
import os
import pickle
from datetime import datetime
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, process_book, save_to_pickle
from llm_chat import (
    LLMConfig, load_config, save_config,
    load_chat_history, save_chat_history, clear_chat_history,
    list_conversations, create_conversation,
    chat_completion_stream,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "library"

ALLOWED_EXTENSIONS = {".epub", ".pdf", ".mobi", ".docx", ".doc"}


@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """Loads the book from the pickle file. Cached to avoid re-reading disk."""
    file_path = os.path.join(BOOKS_DIR, folder_name, "book.pkl")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None


# ===== Library & Reader =====

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []
    if os.path.exists(BOOKS_DIR):
        for item in os.listdir(BOOKS_DIR):
            if item.endswith("_data") and os.path.isdir(os.path.join(BOOKS_DIR, item)):
                book = load_book_cached(item)
                if book:
                    fmt = os.path.splitext(book.source_file)[1].lstrip(".").upper() if book.source_file else "EPUB"
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine),
                        "format": fmt or "EPUB",
                    })
    return templates.TemplateResponse("library.html", {"request": request, "books": books})


@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(request: Request, book_id: str):
    """Helper to just go to chapter 0."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    is_pdf = book.source_file.lower().endswith(".pdf")
    pdf_needs_reprocess = is_pdf and not os.path.exists(
        os.path.join(BOOKS_DIR, book_id, "source.pdf")
    )
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "all_chapters": book.spine,
        "initial_chapter": 0,
        "book_id": book_id,
        "is_pdf": is_pdf,
        "pdf_needs_reprocess": pdf_needs_reprocess,
    })


@app.get("/read/{book_id}/source.pdf")
async def serve_pdf_source(book_id: str):
    """Serves the original PDF file for PDF.js client-side rendering."""
    safe_book_id = os.path.basename(book_id)
    pdf_path = os.path.join(BOOKS_DIR, safe_book_id, "source.pdf")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF source not found")
    return FileResponse(pdf_path, media_type="application/pdf")


@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    is_pdf = book.source_file.lower().endswith(".pdf")
    pdf_needs_reprocess = is_pdf and not os.path.exists(
        os.path.join(BOOKS_DIR, book_id, "source.pdf")
    )
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "all_chapters": book.spine,
        "initial_chapter": chapter_index,
        "book_id": book_id,
        "is_pdf": is_pdf,
        "pdf_needs_reprocess": pdf_needs_reprocess,
    })


@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """Serves images for a book."""
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)
    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)
    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(img_path)


# ===== Upload =====

@app.post("/api/upload")
async def upload_book(file: UploadFile = File(...)):
    """Upload and process a book file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # Save uploaded file
    safe_name = "".join(c for c in file.filename if c.isalpha() or c.isdigit() or c in "._- ").strip()
    upload_path = os.path.join(BOOKS_DIR, safe_name)

    try:
        content = await file.read()
        with open(upload_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Process the book
    output_dir = os.path.splitext(safe_name)[0] + "_data"
    output_path = os.path.join(BOOKS_DIR, output_dir)

    try:
        book_obj = process_book(upload_path, output_path)
        save_to_pickle(book_obj, output_path)
        load_book_cached.cache_clear()
    except Exception as e:
        # Clean up on failure
        if os.path.exists(upload_path):
            os.remove(upload_path)
        raise HTTPException(status_code=500, detail=f"Failed to process book: {e}")

    return {"success": True, "book_id": output_dir}


# ===== Chat API =====

@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """Send a message and stream the LLM response token by token (SSE)."""
    body = await request.json()
    book_id = body.get("book_id")
    chapter_index = body.get("chapter_index", 0)
    user_message = body.get("message", "").strip()
    selected_text = body.get("selected_text", "").strip()
    conv_id = body.get("conv_id")
    model_override = body.get("model_override", "").strip() or None

    if not book_id or not user_message:
        raise HTTPException(status_code=400, detail="book_id and message are required")

    config = load_config()
    if not config.is_configured:
        raise HTTPException(
            status_code=400,
            detail="LLM is not configured. Click the gear icon to set up."
        )

    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=400, detail="Invalid chapter index")

    chapter = book.spine[chapter_index]
    chapter_text = chapter.text[:8000]

    history = load_chat_history(book_id, conv_id)
    system_prompt = (
        f"You are a helpful reading assistant for the book '{book.metadata.title}'. "
        f"The user is currently reading Chapter {chapter_index + 1}: '{chapter.title}'. "
        f"Here is the chapter content for context:\n\n{chapter_text}\n\n"
        "Answer questions about this chapter and the book. Be concise and helpful."
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        content = msg["content"]
        if msg["role"] == "user" and msg.get("selected_text"):
            content = f'[Highlighted passage: "{msg["selected_text"]}"]\n\n{content}'
        messages.append({"role": msg["role"], "content": content})
    user_content = user_message
    if selected_text:
        user_content = f'[Highlighted passage: "{selected_text}"]\n\n{user_message}'
    messages.append({"role": "user", "content": user_content})

    async def generate():
        tokens = []
        try:
            async for token in chat_completion_stream(config, messages, model_override):
                tokens.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # Save full response to history after streaming completes
        response_text = "".join(tokens)
        now = datetime.now().isoformat()
        user_entry = {"role": "user", "content": user_message, "chapter": chapter_index, "timestamp": now}
        if selected_text:
            user_entry["selected_text"] = selected_text
        history.append(user_entry)
        history.append({"role": "assistant", "content": response_text, "chapter": chapter_index, "timestamp": now})
        save_chat_history(book_id, history, conv_id)

        yield f"data: {json.dumps({'done': True, 'chapter': chapter_index})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/chat/conversations/{book_id}")
async def get_conversations(book_id: str):
    """List all conversations for a book."""
    convs = list_conversations(book_id)
    return {"conversations": convs}


@app.post("/api/chat/conversations/{book_id}")
async def new_conversation(book_id: str):
    """Create a new empty conversation for a book."""
    conv_id = create_conversation(book_id)
    return {"conv_id": conv_id}


@app.get("/api/chat/history/{book_id}")
async def get_chat_history(book_id: str, conv_id: Optional[str] = None):
    """Retrieve chat history for a book (optionally scoped to a conversation)."""
    history = load_chat_history(book_id, conv_id)
    return {"history": history}


@app.delete("/api/chat/history/{book_id}")
async def delete_chat_history(book_id: str, conv_id: Optional[str] = None):
    """Clear chat history (optionally scoped to a conversation)."""
    clear_chat_history(book_id, conv_id)
    return {"success": True}


# ===== Settings API =====

@app.get("/api/settings")
async def get_settings():
    """Get current settings (API key masked)."""
    config = load_config()
    return {
        "provider": config.provider,
        "model": config.model,
        "models": config.models,
        "api_key_set": bool(config.api_key),
        "endpoint": config.endpoint,
        "api_version": config.api_version,
    }


@app.put("/api/settings")
async def update_settings(request: Request):
    """Update Azure OpenAI settings."""
    body = await request.json()
    config = load_config()

    for key in ("provider", "model", "endpoint", "api_version"):
        if key in body:
            setattr(config, key, body[key])
    if "models" in body and isinstance(body["models"], list):
        config.models = [str(m) for m in body["models"] if m]
    if "api_key" in body and body["api_key"]:
        config.api_key = body["api_key"]

    save_config(config)
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
