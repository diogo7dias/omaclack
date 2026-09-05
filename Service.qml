import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// SoundTap service: owns the soundtapd helper, the control socket, and the
// user's settings. Lives for the shell's lifetime (manifest keepLoaded: true).
// The bar widget and panel only read these properties and call the set* functions.
Item {
  id: root

  // Injected by omarchy-shell.
  property var shell: null
  property var manifest: null

  readonly property string pluginId: "io.github.ddm.soundtap"
  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "")
  readonly property string home: Quickshell.env("HOME")
  readonly property string runtimeDir: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/soundtap"
  readonly property string socketPath: runtimeDir + "/ctl.sock"
  readonly property string configPath: home + "/.config/omarchy/soundtap.json"

  // ---- user state (persisted) ----
  property bool enabled: true
  property int volume: 70            // keyboard
  property int mouseVolume: 70
  property string currentPack: "mx-blue"
  property bool mouseEnabled: true
  property string mousePack: "logitech"
  property var denylist: []          // lower-cased Wayland app ids

  // ---- daemon state (live) ----
  property var packs: []             // [{id, name, credit, source}] from the daemon
  property var mousePacks: []
  function metaFor(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i]
    return null
  }
  readonly property var currentPackMeta: metaFor(packs, currentPack)
  readonly property var mousePackMeta: metaFor(mousePacks, mousePack)
  property bool connected: false
  property bool daemonRunning: false
  property bool inputDenied: false   // daemon could not open /dev/input (not in `input` group)
  property bool streamOk: false
  property var lastEvent: null
  property var latencies: []         // last 50 ms values, newest last
  readonly property real lastLatency: latencies.length ? latencies[latencies.length - 1] : 0
  readonly property real avgLatency: {
    if (!latencies.length) return 0
    var s = 0
    for (var i = 0; i < latencies.length; i++) s += latencies[i]
    return s / latencies.length
  }

  // Focused app, for the ignore list. ToplevelManager tracks the active
  // Wayland toplevel; appId is the desktop-style id (e.g. "org.gnome.Nautilus").
  readonly property var activeToplevel: ToplevelManager.activeToplevel
  readonly property string activeApp: activeToplevel && activeToplevel.appId ? String(activeToplevel.appId).toLowerCase() : ""
  readonly property bool suppressed: activeApp !== "" && denylist.indexOf(activeApp) >= 0
  readonly property bool effectiveMuted: !enabled || suppressed

  onEffectiveMutedChanged: send({ cmd: "mute", toggle: effectiveMuted })

  // ---- public API ----
  function setEnabled(v) { enabled = !!v; save() }
  function toggle() { setEnabled(!enabled) }
  function setVolume(v) {
    volume = Math.max(0, Math.min(100, Math.round(Number(v) || 0)))
    send({ cmd: "volume", value: volume })
    save()
  }
  function setMouseVolume(v) {
    mouseVolume = Math.max(0, Math.min(100, Math.round(Number(v) || 0)))
    send({ cmd: "mousevolume", value: mouseVolume })
    save()
  }
  function setPack(name) {
    if (!name) return
    currentPack = String(name)
    send({ cmd: "load", pack: currentPack })
    save()
  }
  function setMousePack(name) {
    if (!name) return
    mousePack = String(name)
    send({ cmd: "mousepack", pack: mousePack })
    save()
  }
  function setMouseEnabled(v) {
    mouseEnabled = !!v
    send({ cmd: "mouse", value: mouseEnabled })
    save()
  }
  function setDenylistText(text) {
    var list = String(text || "").split(",").map(function(s) { return s.trim().toLowerCase() })
      .filter(function(s) { return s !== "" })
    denylist = list
    save()
  }
  function denylistText() { return denylist.join(", ") }
  function preview(key) { send({ cmd: "play", key: key === undefined ? 30 : key }) }
  function ping() { send({ cmd: "ping", t: Date.now() }) }

  // ---- persistence ----
  property bool configLoaded: false

  FileView {
    id: configFile
    path: root.configPath
    printErrors: false
    atomicWrites: true
    onLoaded: root.applyConfig(text())
    onLoadFailed: { root.configLoaded = true; root.save() }
  }

  function applyConfig(text) {
    try {
      var c = JSON.parse(text)
      if (typeof c.enabled === "boolean") enabled = c.enabled
      if (typeof c.volume === "number") volume = Math.max(0, Math.min(100, Math.round(c.volume)))
      if (typeof c.mouseVolume === "number") mouseVolume = Math.max(0, Math.min(100, Math.round(c.mouseVolume)))
      if (typeof c.pack === "string" && c.pack) currentPack = c.pack
      if (typeof c.mouse === "boolean") mouseEnabled = c.mouse
      if (typeof c.mousePack === "string" && c.mousePack) mousePack = c.mousePack
      if (Array.isArray(c.denylist)) denylist = c.denylist.map(function(s) { return String(s).toLowerCase() })
    } catch (e) { /* corrupt file: keep defaults, overwrite on next save */ }
    configLoaded = true
    pushState()
  }

  function save() {
    if (!configLoaded) return
    configFile.setText(JSON.stringify({
      version: 1, enabled: enabled, volume: volume, pack: currentPack, mouse: mouseEnabled,
      mousePack: mousePack, mouseVolume: mouseVolume, denylist: denylist
    }, null, 2) + "\n")
  }

  // ---- daemon process ----
  // The daemon's stdin/stdout pipe pair is the control channel: JSON lines in,
  // replies and key events out. No socket to poll or reconnect; the pipe
  // exists exactly as long as the process does, and closing it (shell exit)
  // is what tells the daemon to quit. bin/soundtapd still binds
  // $XDG_RUNTIME_DIR/soundtap/ctl.sock for CLI/tests.
  Process {
    id: daemon
    command: ["python3", root.pluginDir + "bin/soundtapd", "--socket=" + root.socketPath]
    stdinEnabled: true
    running: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.handleLine(line) }
    }
    onStarted: root.daemonRunning = true
    onExited: function(code, status) {
      root.daemonRunning = false
      root.connected = false
      // Exit 2: another soundtapd still holds the socket lock (a previous
      // shell's daemon that has not noticed its pipe closing yet). Either way,
      // try again shortly.
      restartTimer.interval = code === 2 ? 1500 : 2000
      restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: 2000
    onTriggered: daemon.running = true
  }

  function send(obj) {
    if (!daemon.running) return
    try { daemon.write(JSON.stringify(obj) + "\n") } catch (e) { }
  }

  // Full resync after (re)connect or config load. The daemon keeps no config of its own.
  function pushState() {
    if (!connected || !configLoaded) return
    send({ cmd: "load", pack: currentPack })
    send({ cmd: "volume", value: volume })
    send({ cmd: "mousevolume", value: mouseVolume })
    send({ cmd: "mouse", value: mouseEnabled })
    send({ cmd: "mousepack", pack: mousePack })
    send({ cmd: "mute", toggle: effectiveMuted })
    send({ cmd: "status" })
  }

  function handleLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (!msg) return
    if (msg.evt === "key") {
      lastEvent = msg
      pushLatency(msg.latency_ms)
      return
    }
    if (msg.pong !== undefined && msg.pong !== null) {
      pushLatency(Date.now() - Number(msg.pong))
    }
    if (Array.isArray(msg.packs)) packs = msg.packs
    if (Array.isArray(msg.mouse_packs)) mousePacks = msg.mouse_packs
    if (msg.evt === "hello") {
      inputDenied = msg.denied === true
      streamOk = msg.stream === true
      connected = true
      pushState()
    }
    if (typeof msg.stream === "boolean") streamOk = msg.stream
    if (msg.ok === false && msg.error && String(msg.error).indexOf("sounds/") >= 0) {
      // Configured pack vanished from disk: fall back to the default, else the first one available.
      var err = String(msg.error)
      if (err.indexOf("sounds/mouse/") >= 0) {
        if (mousePacks.length) setMousePack(metaFor(mousePacks, "logitech") ? "logitech" : mousePacks[0].id)
      } else if (packs.length) {
        setPack(metaFor(packs, "mx-blue") ? "mx-blue" : packs[0].id)
      }
    }
  }

  function pushLatency(ms) {
    var v = Number(ms)
    if (isNaN(v)) return
    var next = latencies.slice(Math.max(0, latencies.length - 49))
    next.push(Math.round(v * 10) / 10)
    latencies = next
  }

  Component.onDestruction: {
    send({ cmd: "quit" })
    daemon.running = false
  }
}
