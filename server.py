import os
import pickle
from datetime import datetime
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, process_book, save_to_pickle
from llm_chat import (
    AzureConfig, load_config, save_config,
    load_chat_history, save_chat_history, clear_chat_history,
    chat_completion,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "."

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
            if item.endswith("_data") and os.path.isdir(item):
                book = load_book_cached(item)
                if book:
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine)
                    })
    return templates.TemplateResponse("library.html", {"request": request, "books": books})


@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(request: Request, book_id: str):
    """Helper to just go to chapter 0."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "all_chapters": book.spine,
        "initial_chapter": 0,
        "book_id": book_id,
    })


@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "all_chapters": book.spine,
        "initial_chapter": chapter_index,
        "book_id": book_id,
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
    """Send a message and get an LLM response with chapter context."""
    body = await request.json()
    book_id = body.get("book_id")
    chapter_index = body.get("chapter_index", 0)
    user_message = body.get("message", "").strip()

    if not book_id or not user_message:
        raise HTTPException(status_code=400, detail="book_id and message are required")

    config = load_config()
    if not config.is_configured:
        return JSONResponse(
            status_code=400,
            content={"detail": "Azure OpenAI is not configured. Click the gear icon to set up."}
        )

    # Get chapter context
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=400, detail="Invalid chapter index")

    chapter = book.spine[chapter_index]
    chapter_text = chapter.text[:8000]  # Limit context size

    # Build messages
    history = load_chat_history(book_id)
    system_prompt = (
        f"You are a helpful reading assistant for the book '{book.metadata.title}'. "
        f"The user is currently reading Chapter {chapter_index + 1}: '{chapter.title}'. "
        f"Here is the chapter content for context:\n\n{chapter_text}\n\n"
        "Answer questions about this chapter and the book. Be concise and helpful."
    )

    messages = [{"role": "system", "content": system_prompt}]

    # Add recent history (last 10 messages)
    recent = history[-10:]
    for msg in recent:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    try:
        response_text = await chat_completion(config, messages)
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"LLM error: {e}"})

    # Save to history
    now = datetime.now().isoformat()
    history.append({"role": "user", "content": user_message, "chapter": chapter_index, "timestamp": now})
    history.append({"role": "assistant", "content": response_text, "chapter": chapter_index, "timestamp": now})
    save_chat_history(book_id, history)

    return {"response": response_text, "chapter": chapter_index}


@app.get("/api/chat/history/{book_id}")
async def get_chat_history(book_id: str):
    """Retrieve chat history for a book."""
    history = load_chat_history(book_id)
    return {"history": history}


@app.delete("/api/chat/history/{book_id}")
async def delete_chat_history(book_id: str):
    """Clear chat history for a book."""
    clear_chat_history(book_id)
    return {"success": True}


# ===== Settings API =====

@app.get("/api/settings")
async def get_settings():
    """Get current settings (API key masked)."""
    config = load_config()
    return {
        "endpoint": config.endpoint,
        "api_key_set": bool(config.api_key),
        "deployment_name": config.deployment_name,
        "api_version": config.api_version,
    }


@app.put("/api/settings")
async def update_settings(request: Request):
    """Update Azure OpenAI settings."""
    body = await request.json()
    config = load_config()

    if "endpoint" in body:
        config.endpoint = body["endpoint"]
    if "api_key" in body and body["api_key"]:
        config.api_key = body["api_key"]
    if "deployment_name" in body:
        config.deployment_name = body["deployment_name"]
    if "api_version" in body:
        config.api_version = body["api_version"]

    save_config(config)
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
