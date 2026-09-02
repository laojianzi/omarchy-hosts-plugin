import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string pluginDir: Quickshell.env("OMARCHY_HOSTS_PLUGIN_DIR")
    || (home + "/.config/omarchy/plugins/io.omarchy.hosts")
  readonly property string cliPath: pluginDir + "/bin/omarchy-hosts"
  readonly property string pythonPath: "/usr/bin/python"
  readonly property int maxStdoutChars: 8 * 1024 * 1024
  readonly property int maxStderrChars: 128 * 1024
  readonly property int maxErrorChars: 4096
  readonly property int normalDeadlineMs: 30000
  readonly property int privilegedDeadlineMs: 195000

  property var profiles: []
  property var status: ({ kind: "idle", label: "Loading…", canApply: false, canUndo: false, error: null })
  property var summary: ({ profileCount: 0, enabledProfileCount: 0, configuredEntryCount: 0 })
  property var plan: null
  property var helper: ({ installed: false, policyInstalled: false, pkexecInstalled: false, ready: false })
  property var lastApply: null
  property bool loaded: false
  property bool busy: false
  property string busyAction: ""
  property string error: ""
  property string notice: ""
  property string stdinPayload: ""
  property int revision: 0
  property bool refreshQueued: false

  // Process-lifetime state. Output is streamed into explicitly bounded
  // buffers so unexpected child output cannot grow without limit.
  property string stdoutBuffer: ""
  property string stderrBuffer: ""
  property bool stopping: false
  property string stopMessage: ""
  property bool destroying: false
  property int pendingExitCode: 0

  signal changed()
  signal operationSucceeded(string action, var data)
  signal operationFailed(string action, string message)

  function profileById(id) {
    var key = String(id || "")
    for (var i = 0; i < profiles.length; i++) {
      if (String(profiles[i].id) === key) return profiles[i]
    }
    return null
  }

  function profileByRef(ref) {
    var key = String(ref || "").trim()
    if (key === "") return null
    var direct = profileById(key)
    if (direct) return direct
    for (var i = 0; i < profiles.length; i++) {
      if (String(profiles[i].name) === key) return profiles[i]
    }
    return null
  }

  function _deadlineFor(action) {
    return action === "apply" || action === "undo"
      ? privilegedDeadlineMs
      : normalDeadlineMs
  }

  function _displayExcerpt(value) {
    var text = String(value || "").trim()
    if (text.length <= maxErrorChars) return text
    return text.slice(0, maxErrorChars) + "\n… output truncated …"
  }

  function _signalBackend(signalNumber) {
    if (!backend.running) return
    try {
      backend.signal(signalNumber)
    } catch (e) {
      // Older Quickshell builds still stop a Process when running is cleared.
      backend.running = false
    }
  }

  function _requestStop(message) {
    if (!busy || stopping) return
    stopping = true
    stopMessage = String(message || "Backend operation cancelled")
    operationTimer.stop()
    _signalBackend(15)
    if (!backend.running) {
      pendingExitCode = -1
      finishTimer.restart()
      return
    }
    killTimer.restart()
  }

  function _appendOutput(stderrStream, data) {
    if (!busy || stopping) return
    var chunk = String(data || "")
    if (stderrStream) {
      if (stderrBuffer.length + chunk.length > maxStderrChars) {
        _requestStop("Backend stderr exceeded the safety limit")
        return
      }
      stderrBuffer += chunk
    } else {
      if (stdoutBuffer.length + chunk.length > maxStdoutChars) {
        _requestStop("Backend response exceeded the safety limit")
        return
      }
      stdoutBuffer += chunk
    }
  }

  function _run(action, args, input) {
    if (busy) {
      if (action === "refresh") refreshQueued = true
      return false
    }
    busy = true
    busyAction = action
    error = ""
    stdinPayload = input ? String(input) : ""
    stdoutBuffer = ""
    stderrBuffer = ""
    stopping = false
    stopMessage = ""
    finishTimer.stop()
    killTimer.stop()
    operationTimer.interval = _deadlineFor(action)
    operationTimer.restart()

    var command = [pythonPath, "-I", "-B", cliPath, "--json"]
    for (var i = 0; i < args.length; i++) command.push(String(args[i]))
    backend.command = command
    backend.running = true
    return true
  }

  function refresh() { return _run("refresh", ["ui-state"], "") }
  function saveProfile(payload) { return _run("save", ["profile-save", "-"], JSON.stringify(payload) + "\n") }
  function toggleProfile(id, enabled) { return _run("toggle", ["profile-toggle", id, enabled ? "true" : "false"], "") }
  function deleteProfile(id) { return _run("delete", ["profile-delete", id], "") }
  function applyChanges() {
    var args = ["apply"]
    if (plan && plan.currentSha256 && plan.configSha256) {
      args.push("--expect-base-sha256", String(plan.currentSha256))
      args.push("--expect-config-sha256", String(plan.configSha256))
    }
    return _run("apply", args, "")
  }
  function undo() {
    var args = ["undo"]
    if (status && status.undoAfterSha256) {
      args.push("--expect-after-sha256", String(status.undoAfterSha256))
    }
    return _run("undo", args, "")
  }

  function _applyState(data) {
    profiles = data && data.profiles ? data.profiles : []
    status = data && data.status ? data.status : ({
      kind: "error", label: "Backend returned incomplete state", canApply: false, canUndo: false, error: null
    })
    summary = data && data.summary ? data.summary : ({
      profileCount: 0, enabledProfileCount: 0, configuredEntryCount: 0
    })
    plan = data ? data.plan : null
    helper = data && data.helper ? data.helper : ({
      installed: false, policyInstalled: false, pkexecInstalled: false, ready: false
    })
    lastApply = data ? data.lastApply : null
    loaded = true
    revision += 1
    changed()
  }

  function _markRefreshFailure(message) {
    loaded = true
    status = ({
      kind: "error",
      label: "Backend unavailable",
      canApply: false,
      canUndo: false,
      error: ({ code: "backend_failed", message: message, details: ({}) })
    })
    revision += 1
    changed()
  }

  function _finish(code) {
    if (!busy) return
    operationTimer.stop()
    killTimer.stop()

    var action = busyAction
    var raw = String(stdoutBuffer || "").trim()
    var err = String(stderrBuffer || "").trim()
    var wasStopped = stopping
    var stoppedMessage = stopMessage
    var envelope = null
    if (!wasStopped && raw !== "") {
      var lines = raw.split("\n")
      for (var i = lines.length - 1; i >= 0; i--) {
        var line = lines[i].trim()
        if (line.charAt(0) !== "{") continue
        try { envelope = JSON.parse(line); break } catch (e) { }
      }
    }

    busy = false
    busyAction = ""
    stdinPayload = ""
    stdoutBuffer = ""
    stderrBuffer = ""
    stopping = false
    stopMessage = ""
    var shouldRefresh = false

    if (wasStopped || !envelope || envelope.ok !== true) {
      var message = wasStopped ? stoppedMessage : "Backend command failed"
      if (!wasStopped && envelope && envelope.error && envelope.error.message) message = _displayExcerpt(envelope.error.message)
      else if (!wasStopped && err !== "") message = _displayExcerpt(err)
      else if (!wasStopped && raw !== "") message = _displayExcerpt(raw)
      else if (!wasStopped && code !== 0) message = "Backend exited with code " + code
      error = message
      if (action === "refresh") _markRefreshFailure(message)
      operationFailed(action, message)
    } else {
      var data = envelope.data
      if (action === "refresh") {
        _applyState(data)
      } else {
        if (action === "save") notice = "Profile saved"
        else if (action === "toggle") notice = "Profile state updated"
        else if (action === "delete") notice = "Profile deleted"
        else if (action === "apply") notice = data && data.message ? String(data.message) : "Hosts applied"
        else if (action === "undo") notice = data && data.message ? String(data.message) : "Last apply undone"
        if (notice !== "") noticeTimer.restart()
        operationSucceeded(action, data)
        shouldRefresh = true
      }
    }

    if (!destroying && (refreshQueued || shouldRefresh)) {
      refreshQueued = false
      Qt.callLater(function() { if (!root.busy) root.refresh() })
    }
  }

  Component.onCompleted: refresh()
  Component.onDestruction: {
    destroying = true
    refreshQueued = false
    operationTimer.stop()
    finishTimer.stop()
    killTimer.stop()
    if (backend.running) {
      _signalBackend(15)
      // Clearing running is the compatibility fallback and also asks the
      // Process object to stop before its owning QML component disappears.
      backend.running = false
    }
  }

  Process {
    id: backend
    command: []
    running: false
    stdinEnabled: true
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(data) { root._appendOutput(false, data) }
    }
    stderr: SplitParser {
      splitMarker: ""
      onRead: function(data) { root._appendOutput(true, data) }
    }
    onStarted: if (root.stdinPayload !== "") backend.write(root.stdinPayload)
    onExited: function(code, status) {
      root.pendingExitCode = code
      // Let the stream parsers deliver any final chunk before parsing the envelope.
      finishTimer.restart()
    }
  }

  Timer {
    id: finishTimer
    interval: 50
    repeat: false
    onTriggered: root._finish(root.pendingExitCode)
  }

  Timer {
    id: operationTimer
    repeat: false
    onTriggered: root._requestStop("Backend operation timed out")
  }

  Timer {
    id: killTimer
    interval: 3000
    repeat: false
    onTriggered: {
      if (backend.running) {
        root._signalBackend(9)
        backend.running = false
      }
      root.pendingExitCode = -9
      finishTimer.restart()
    }
  }

  Timer {
    interval: 15000
    repeat: true
    running: true
    onTriggered: if (!root.busy) root.refresh()
  }

  Timer {
    id: noticeTimer
    interval: 3600
    repeat: false
    onTriggered: root.notice = ""
  }
}
