import Cocoa

class AppDelegate: NSObject, NSApplicationDelegate {
    var serverProcess: Process?
    let port = "8000"

    // Resolve project directory: the app sits inside it, or use bundle resource
    lazy var projectDir: String = {
        // The app is expected to be run from the project directory or installed alongside it
        // We embed the project path at build time via an environment variable in Info.plist
        if let path = Bundle.main.infoDictionary?["ProjectDir"] as? String, !path.isEmpty {
            return path
        }
        // Fallback: assume the .app is inside the project directory
        let bundlePath = Bundle.main.bundlePath
        return (bundlePath as NSString).deletingLastPathComponent
    }()

    lazy var uvPath: String = {
        // Search common uv locations
        let candidates = [
            "\(NSHomeDirectory())/.local/bin/uv",
            "\(NSHomeDirectory())/.cargo/bin/uv",
            "/usr/local/bin/uv",
            "/opt/homebrew/bin/uv",
        ]
        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        // Try to find via shell
        let proc = Process()
        let pipe = Pipe()
        proc.executableURL = URL(fileURLWithPath: "/bin/bash")
        proc.arguments = ["-lc", "which uv"]
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !output.isEmpty && FileManager.default.isExecutableFile(atPath: output) {
            return output
        }
        return "uv" // last resort, hope it's on PATH
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        startServer()

        DispatchQueue.global().async {
            for _ in 0..<30 {
                if self.isServerReady() { break }
                Thread.sleep(forTimeInterval: 0.5)
            }
            DispatchQueue.main.async {
                NSWorkspace.shared.open(URL(string: "http://localhost:\(self.port)")!)
            }

            Thread.sleep(forTimeInterval: 3)
            self.watchForTabClosed()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        closeBrowserTab()
        killServer()
    }

    func startServer() {
        let kill = Process()
        kill.executableURL = URL(fileURLWithPath: "/bin/bash")
        kill.arguments = ["-c", "lsof -ti:\(port) | xargs kill -9 2>/dev/null; true"]
        try? kill.run()
        kill.waitUntilExit()

        // Ensure log file exists
        let logPath = "\(projectDir)/marginalia.log"
        FileManager.default.createFile(atPath: logPath, contents: nil)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: uvPath)
        process.arguments = ["run", "uvicorn", "server:app", "--port", port]
        process.currentDirectoryURL = URL(fileURLWithPath: projectDir)

        let logFile = FileHandle(forWritingAtPath: logPath) ?? FileHandle.nullDevice
        process.standardOutput = logFile
        process.standardError = logFile

        try? process.run()
        serverProcess = process
    }

    func killServer() {
        serverProcess?.terminate()
        let kill = Process()
        kill.executableURL = URL(fileURLWithPath: "/bin/bash")
        kill.arguments = ["-c", "lsof -ti:\(port) | xargs kill -9 2>/dev/null; true"]
        try? kill.run()
        kill.waitUntilExit()
    }

    func closeBrowserTab() {
        let browsers: [(id: String, name: String)] = [
            ("com.microsoft.edgemac", "Microsoft Edge"),
            ("com.google.Chrome", "Google Chrome"),
            ("com.apple.Safari", "Safari"),
            ("com.brave.Browser", "Brave Browser"),
            ("com.vivaldi.Vivaldi", "Vivaldi"),
            ("company.thebrowser.Browser", "Arc"),
        ]

        for browser in browsers {
            if NSWorkspace.shared.runningApplications.contains(where: { $0.bundleIdentifier == browser.id }) {
                let script = """
                tell application "\(browser.name)"
                    repeat with w in windows
                        repeat with t in tabs of w
                            if URL of t contains "localhost:\(port)" then
                                close t
                            end if
                        end repeat
                    end repeat
                end tell
                """
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                proc.arguments = ["-e", script]
                try? proc.run()
                proc.waitUntilExit()
            }
        }
    }

    func watchForTabClosed() {
        while true {
            Thread.sleep(forTimeInterval: 2)
            if !isTabOpen() {
                DispatchQueue.main.async {
                    NSApplication.shared.terminate(nil)
                }
                return
            }
        }
    }

    func isTabOpen() -> Bool {
        let browsers: [(id: String, name: String)] = [
            ("com.microsoft.edgemac", "Microsoft Edge"),
            ("com.google.Chrome", "Google Chrome"),
            ("com.apple.Safari", "Safari"),
            ("com.brave.Browser", "Brave Browser"),
            ("com.vivaldi.Vivaldi", "Vivaldi"),
            ("company.thebrowser.Browser", "Arc"),
        ]

        for browser in browsers {
            if NSWorkspace.shared.runningApplications.contains(where: { $0.bundleIdentifier == browser.id }) {
                let script = """
                tell application "\(browser.name)"
                    repeat with w in windows
                        repeat with t in tabs of w
                            if URL of t contains "localhost:\(port)" then return true
                        end repeat
                    end repeat
                end tell
                return false
                """
                let proc = Process()
                let pipe = Pipe()
                proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                proc.arguments = ["-e", script]
                proc.standardOutput = pipe
                proc.standardError = FileHandle.nullDevice
                try? proc.run()
                proc.waitUntilExit()
                let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if output == "true" { return true }
            }
        }
        return false
    }

    func isServerReady() -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        task.arguments = ["-s", "http://localhost:\(port)"]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        try? task.run()
        task.waitUntilExit()
        return task.terminationStatus == 0
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
