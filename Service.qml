import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.Pipewire

// Omaclack service: owns the omaclackd helper and the user's settings. Lives for
// the shell's lifetime (manifest keepLoaded: true). The bar widget and panel
// only read these properties and call the set* functions.
Item {
  id: root

  // Injected by omarchy-shell.
  property var shell: null
  property var manifest: null

  readonly property string pluginId: "io.github.diogo7dias.omaclack"
  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "")
  readonly property string home: Quickshell.env("HOME")
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (home + "/.config")
  readonly property string runtimeDir: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/omaclack"
  readonly property string socketPath: runtimeDir + "/ctl.sock"
  readonly property string configPath: configHome + "/omarchy/omaclack.json"
  readonly property string userPacksDir: configHome + "/omarchy/omaclack/packs"

  // ---- user state (persisted) ----
  property bool enabled: true
  property int volume: 70            // keyboard
  property int mouseVolume: 70
  property string currentPack: "mx-blue"
  property bool mouseEnabled: true
  property string mousePack: "logitech"
  property bool velocity: true       // louder when typing fast
  property bool releaseSounds: true  // key-up samples
  property string room: "none"
  property var denylist: []          // lower-cased Wayland app ids
  property bool muteInMeetings: true // mute while any app records the microphone
  property bool quietHours: false
  property string quietFrom: "22:00"
  property string quietTo: "07:00"
  property int quietPercent: 40      // volume scale inside quiet hours
  property var themePacks: ({})      // Omarchy theme slug -> pack id

  // ---- daemon state (live) ----
  property var packs: []             // [{id, name, credit, source, release, user}]
  property var mousePacks: []
  property var rooms: []             // [{id, name}]
  property var devices: []           // keyboard names that produced presses, busiest first
  property bool connected: false
  property bool daemonRunning: false
  property bool inputDenied: false   // daemon could not open /dev/input (not in `input` group)
  property bool streamOk: false
  property var lastEvent: null
  property var latencies: []         // last 50 ms values, newest last
  property var stats: ({ kpm: 0, total: 0, window: 0, rhythm: [], top: [] })
  property string lastTheme: ""
  readonly property real lastLatency: latencies.length ? latencies[latencies.length - 1] : 0
  readonly property real avgLatency: {
    if (!latencies.length) return 0
    var s = 0
    for (var i = 0; i < latencies.length; i++) s += latencies[i]
    return s / latencies.length
  }

  function metaFor(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i]
    return null
  }
  readonly property var currentPackMeta: metaFor(packs, currentPack)
  readonly property var mousePackMeta: metaFor(mousePacks, mousePack)

  // Focused app, for the ignore list.
  readonly property var activeToplevel: ToplevelManager.activeToplevel
  readonly property string activeApp: activeToplevel && activeToplevel.appId ? String(activeToplevel.appId).toLowerCase() : ""
  readonly property bool suppressed: activeApp !== "" && denylist.indexOf(activeApp) >= 0

  // Microphone in use: any PipeWire capture stream (an app recording), e.g. a call.
  // Track only capture streams, not every node in the graph.
  readonly property var captureNodes: {
    var n = Pipewire.nodes.values, out = []
    for (var i = 0; i < n.length; i++) {
      if (n[i] && n[i].isStream && n[i].isSink === false) out.push(n[i])
    }
    return out
  }
  PwObjectTracker { objects: root.captureNodes }
  property bool micInUse: false
  function scanMic() {
    var nodes = root.captureNodes
    var found = false
    for (var i = 0; i < nodes.length && !found; i++) {
      var n = nodes[i]
      // Stream/Input/Audio = an app capturing (Quickshell: a stream that is not a sink).
      if (n && n.audio && String(n.name || "").indexOf("omaclack") < 0) found = true
    }
    micInUse = found
  }
  Connections {
    target: Pipewire.nodes
    function onObjectInsertedPost() { Qt.callLater(root.scanMic) }
    function onObjectRemovedPost() { Qt.callLater(root.scanMic) }
  }
  Timer { interval: 5000; running: root.muteInMeetings; repeat: true; triggeredOnStart: true; onTriggered: root.scanMic() }
  readonly property bool meetingMuted: muteInMeetings && micInUse

  // Quiet hours: minute timer flips this; volumes are scaled on the way to the daemon.
  property bool inQuietHours: false
  function minutes(t) {
    var m = /^(\d{1,2}):(\d{2})$/.exec(String(t || ""))
    return m ? Math.min(23, Number(m[1])) * 60 + Math.min(59, Number(m[2])) : -1
  }
  function computeQuiet() {
    if (!quietHours) return false
    var a = minutes(quietFrom), b = minutes(quietTo)
    if (a < 0 || b < 0 || a === b) return false
    var d = new Date(), now = d.getHours() * 60 + d.getMinutes()
    return a < b ? (now >= a && now < b) : (now >= a || now < b)
  }
  Timer { interval: 30000; running: root.quietHours; repeat: true; triggeredOnStart: true; onTriggered: root.inQuietHours = root.computeQuiet() }
  onQuietHoursChanged: inQuietHours = computeQuiet()
  onQuietFromChanged: inQuietHours = computeQuiet()
  onQuietToChanged: inQuietHours = computeQuiet()
  readonly property real volumeScale: inQuietHours ? quietPercent / 100 : 1
  onVolumeScaleChanged: pushVolumes()
  onQuietPercentChanged: if (inQuietHours) pushVolumes()

  readonly property bool effectiveMuted: !enabled || suppressed || meetingMuted
  onEffectiveMutedChanged: send({ cmd: "mute", toggle: effectiveMuted })

  // ---- public API ----
  function setEnabled(v) { enabled = !!v; save() }
  function toggle() { setEnabled(!enabled) }
  function setVolume(v) {
    volume = Math.max(0, Math.min(100, Math.round(Number(v) || 0)))
    send({ cmd: "volume", value: Math.round(volume * volumeScale) })
    save()
  }
  function setMouseVolume(v) {
    mouseVolume = Math.max(0, Math.min(100, Math.round(Number(v) || 0)))
    send({ cmd: "mousevolume", value: Math.round(mouseVolume * volumeScale) })
    save()
  }
  function pushVolumes() {
    send({ cmd: "volume", value: Math.round(volume * volumeScale) })
    send({ cmd: "mousevolume", value: Math.round(mouseVolume * volumeScale) })
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
  function setMouseEnabled(v) { mouseEnabled = !!v; send({ cmd: "mouse", value: mouseEnabled }); save() }
  function setVelocity(v) { velocity = !!v; send({ cmd: "velocity", value: velocity }); save() }
  function setReleaseSounds(v) { releaseSounds = !!v; send({ cmd: "release", value: releaseSounds }); save() }
  function setRoom(id) { room = String(id || "none"); send({ cmd: "room", value: room }); save() }
  function setMuteInMeetings(v) { muteInMeetings = !!v; save() }
  function setQuietHours(v) { quietHours = !!v; save() }
  function setQuietRange(from, to) {
    if (minutes(from) >= 0) quietFrom = String(from)
    if (minutes(to) >= 0) quietTo = String(to)
    save()
  }
  function setQuietPercent(v) { quietPercent = Math.max(0, Math.min(100, Math.round(Number(v) || 0))); save() }
  function setDenylistText(text) {
    denylist = String(text || "").split(",").map(function(s) { return s.trim().toLowerCase() })
      .filter(function(s) { return s !== "" })
    save()
  }
  function denylistText() { return denylist.join(", ") }
  function bindThemePack(slug, pack) {
    var next = ({})
    for (var k in themePacks) next[k] = themePacks[k]
    if (pack) next[slug] = pack; else delete next[slug]
    themePacks = next
    save()
  }
  function preview(key) { send({ cmd: "play", key: key === undefined ? 30 : key }) }
  function ping() { send({ cmd: "ping", t: Date.now() }) }
  function requestStats() { send({ cmd: "stats" }) }
  function refreshPacks() {
    send({ cmd: "status" })
    // Force the daemon to drop cached samples so a replaced pack on disk is heard.
    if (currentPack) send({ cmd: "load", pack: currentPack })
    if (mousePack) send({ cmd: "mousepack", pack: mousePack })
  }

  // Hardware hint: suggest a pack for the keyboard that is actually typing.
  readonly property string mainDevice: devices.length ? devices[0] : ""
  readonly property var deviceHint: {
    var n = mainDevice.toLowerCase()
    if (!n) return null
    var rules = [
      [/at translated set 2|apple internal|thinkpad|laptop/, "mx-red-pbt", "laptop keyboard: try a quiet linear"],
      [/hhkb|realforce|topre|leopold fc660c/, "topre", "Topre board detected"],
      [/model m|unicomp/, "buckling-spring", "buckling spring board detected"],
      [/keychron|nuphy|ducky|glorious|wooting|varmilo|akko|leopold|drop|corsair|razer|logitech g|steelseries|hyperx|epomaker|royal kludge|rk/, "mx-brown", "mechanical board detected"],
    ]
    for (var i = 0; i < rules.length; i++) {
      if (rules[i][0].test(n) && metaFor(packs, rules[i][1])) return { pack: rules[i][1], text: rules[i][2] }
    }
    return null
  }

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
      if (typeof c.velocity === "boolean") velocity = c.velocity
      if (typeof c.release === "boolean") releaseSounds = c.release
      if (typeof c.room === "string" && c.room) room = c.room
      if (typeof c.muteInMeetings === "boolean") muteInMeetings = c.muteInMeetings
      if (typeof c.quietHours === "boolean") quietHours = c.quietHours
      if (typeof c.quietFrom === "string") quietFrom = c.quietFrom
      if (typeof c.quietTo === "string") quietTo = c.quietTo
      if (typeof c.quietPercent === "number") quietPercent = Math.max(0, Math.min(100, Math.round(c.quietPercent)))
      if (Array.isArray(c.denylist)) denylist = c.denylist.map(function(s) { return String(s).toLowerCase() })
      if (c.themePacks && typeof c.themePacks === "object") themePacks = c.themePacks
    } catch (e) { /* corrupt file: keep defaults, overwrite on next save */ }
    configLoaded = true
    inQuietHours = computeQuiet()
    pushState()
  }

  // Debounce disk writes: sliders fire setVolume on every tick.
  Timer { id: saveDebounce; interval: 250; onTriggered: root.flushSave() }
  function save() { if (!configLoaded) return; saveDebounce.restart() }
  function flushSave() {
    if (!configLoaded) return
    saveDebounce.stop()
    configFile.setText(JSON.stringify({
      version: 2, enabled: enabled, volume: volume, pack: currentPack, mouse: mouseEnabled,
      mousePack: mousePack, mouseVolume: mouseVolume, velocity: velocity, release: releaseSounds,
      room: room, muteInMeetings: muteInMeetings, quietHours: quietHours, quietFrom: quietFrom,
      quietTo: quietTo, quietPercent: quietPercent, denylist: denylist, themePacks: themePacks
    }, null, 2) + "\n")
  }

  // ---- daemon process ----
  // The daemon's stdin/stdout pipe pair is the control channel: JSON lines in,
  // replies and events out. The pipe exists exactly as long as the process
  // does, and closing it (shell exit) tells the daemon to quit. bin/omaclackd
  // also binds $XDG_RUNTIME_DIR/omaclack/ctl.sock for the CLI, tests and hooks.
  Process {
    id: daemon
    command: [root.pluginDir + "bin/omaclackd", "--socket=" + root.socketPath]
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
    send({ cmd: "mousepack", pack: mousePack })
    pushVolumes()
    send({ cmd: "mouse", value: mouseEnabled })
    send({ cmd: "velocity", value: velocity })
    send({ cmd: "release", value: releaseSounds })
    send({ cmd: "room", value: room })
    send({ cmd: "mute", toggle: effectiveMuted })
    send({ cmd: "status" })
  }

  function handleLine(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (!msg) return
    if (msg.evt === "key") {
      lastEvent = { latency_ms: msg.latency_ms }
      pushLatency(msg.latency_ms)
      return
    }
    if (msg.evt === "theme") {
      lastTheme = String(msg.slug || "")
      var p = themePacks[lastTheme]
      if (p && metaFor(packs, p)) setPack(p)
      return
    }
    if (msg.pong !== undefined && msg.pong !== null) pushLatency(Date.now() - Number(msg.pong))
    if (typeof msg.denied === "boolean") inputDenied = msg.denied
    if (Array.isArray(msg.devices)) devices = msg.devices
    if (typeof msg.kpm === "number") { stats = msg; return }
    if (Array.isArray(msg.packs)) packs = msg.packs
    if (Array.isArray(msg.mouse_packs)) mousePacks = msg.mouse_packs
    if (Array.isArray(msg.rooms)) rooms = msg.rooms
    if (msg.evt === "hello") {
      streamOk = msg.stream === true
      connected = true
      pushState()
    }
    if (typeof msg.stream === "boolean") streamOk = msg.stream
    if (msg.ok === false && msg.error && String(msg.error).indexOf("default.opus") >= 0) {
      // Configured pack vanished from disk: fall back to the default, else the first one available.
      var err = String(msg.error)
      if (err.indexOf("/mouse/") >= 0) {
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
    if (saveDebounce.running) root.flushSave()
    send({ cmd: "quit" })
    daemon.running = false
  }
}
