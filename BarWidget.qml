import QtQuick
import qs.Commons
import qs.Ui

// Bar entry: a 22px ripple glyph. Left click toggles the panel, right click
// toggles sound on/off, wheel nudges volume.
BarWidget {
  id: root
  moduleName: "io.github.ddm.soundtap"

  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor("io.github.ddm.soundtap") : null
  readonly property bool soundOn: service ? (service.enabled && !service.suppressed) : false

  // Shape contract the bar uses to route shell summon/hide/toggle to us.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("service" in target) target.service = root.service
  }

  onBarChanged: injectPanel()
  onServiceChanged: injectPanel()

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    active: root.opened
    tooltipText: root.service
      ? (root.soundOn ? "SoundTap · " + root.service.currentPack + " · " + root.service.volume + "%" : "SoundTap · off")
      : "SoundTap · starting"
    iconComponent: ripple

    onPressed: function(b) {
      if (b === Qt.RightButton) { if (root.service) root.service.toggle() }
      else root.togglePanel()
    }
    onWheelMoved: function(delta) {
      if (!root.service) return
      root.service.setVolume(root.service.volume + (delta > 0 ? 5 : -5))
    }
  }

  // Ripple glyph drawn on a Canvas so it follows the bar's live
  // foreground colour (a static near-black SVG would vanish on dark themes).
  Component {
    id: ripple
    Canvas {
      id: canvas
      property color ink: root.bar ? root.bar.barForeground : Color.foreground
      property real dim: root.soundOn ? 1.0 : 0.45
      onInkChanged: requestPaint()
      onDimChanged: requestPaint()
      onWidthChanged: requestPaint()
      onPaint: {
        var ctx = getContext("2d")
        var w = width, h = height, s = w / 22
        ctx.reset()
        ctx.clearRect(0, 0, w, h)
        ctx.globalAlpha = dim
        ctx.strokeStyle = ink
        ctx.fillStyle = ink
        ctx.lineWidth = Math.max(1, 0.9 * s)
        ctx.lineCap = "round"
        var cx = w / 2, cy = h / 2
        ctx.beginPath(); ctx.arc(cx, cy, 1.6 * s, 0, Math.PI * 2); ctx.fill()
        ctx.beginPath(); ctx.arc(cx, cy, 4.6 * s, 0, Math.PI * 2); ctx.stroke()
        // Middle ring: two upper arcs plus a lower arc, gaps at the diagonals.
        ctx.beginPath(); ctx.arc(cx, cy, 6.8 * s, Math.PI * 1.02, Math.PI * 1.34); ctx.stroke()
        ctx.beginPath(); ctx.arc(cx, cy, 6.8 * s, Math.PI * 1.66, Math.PI * 1.98); ctx.stroke()
        ctx.beginPath(); ctx.arc(cx, cy, 6.8 * s, Math.PI * 0.22, Math.PI * 0.78); ctx.stroke()
        // Outer ring: upper arcs only.
        ctx.beginPath(); ctx.arc(cx, cy, 8.8 * s, Math.PI * 1.0, Math.PI * 1.26); ctx.stroke()
        ctx.beginPath(); ctx.arc(cx, cy, 8.8 * s, Math.PI * 1.74, Math.PI * 2.0); ctx.stroke()
      }
    }
  }
}
