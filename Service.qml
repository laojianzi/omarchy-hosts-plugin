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

  function _run(action, args, input) {
    if (busy) {
      if (action === "refresh") refreshQueued = true
      return false
    }
    busy = true
    busyAction = action
    error = ""
    stdinPayload = input ? String(input) : ""
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
    var action = busyAction
    var raw = String(stdoutCollector.text || "").trim()
    var err = String(stderrCollector.text || "").trim()
    var envelope = null
    if (raw !== "") {
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
    var shouldRefresh = false

    if (!envelope || envelope.ok !== true) {
      var message = "Backend command failed"
      if (envelope && envelope.error && envelope.error.message) message = String(envelope.error.message)
      else if (err !== "") message = err
      else if (raw !== "") message = raw
      else if (code !== 0) message = "Backend exited with code " + code
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

    if (refreshQueued || shouldRefresh) {
      refreshQueued = false
      Qt.callLater(function() { if (!root.busy) root.refresh() })
    }
  }

  Component.onCompleted: refresh()

  Process {
    id: backend
    command: []
    running: false
    stdinEnabled: true
    stdout: StdioCollector { id: stdoutCollector; waitForEnd: true }
    stderr: StdioCollector { id: stderrCollector; waitForEnd: true }
    onStarted: if (root.stdinPayload !== "") backend.write(root.stdinPayload)
    onExited: function(code, status) { root._finish(code) }
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
