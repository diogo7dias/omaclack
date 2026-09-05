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

  // Glyph: a click waveform inside a keycap, drawn on a Canvas so it follows
  // the bar's live foreground colour and dims when sound is off.
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
        ctx.lineWidth = Math.max(1, 0.9 * s)
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        // Keycap: rounded square, 15 units wide.
        var x = 3.5 * s, y = 3.5 * s, k = 15 * s, r = 2.5 * s
        ctx.beginPath()
        ctx.moveTo(x + r, y)
        ctx.lineTo(x + k - r, y); ctx.arcTo(x + k, y, x + k, y + r, r)
        ctx.lineTo(x + k, y + k - r); ctx.arcTo(x + k, y + k, x + k - r, y + k, r)
        ctx.lineTo(x + r, y + k); ctx.arcTo(x, y + k, x, y + k - r, r)
        ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r)
        ctx.closePath()
        ctx.stroke()
        // Waveform: flat lead-in, sharp attack, decaying swing, flat tail.
        var pts = [[6, 11], [7.2, 11], [8.2, 8.6], [9.4, 14.2], [10.6, 6.2], [11.8, 14.6], [12.9, 8.4], [13.9, 12.4], [14.8, 11], [16, 11]]
        ctx.beginPath()
        for (var i = 0; i < pts.length; i++) {
          if (i === 0) ctx.moveTo(pts[i][0] * s, pts[i][1] * s)
          else ctx.lineTo(pts[i][0] * s, pts[i][1] * s)
        }
        ctx.stroke()
      }
    }
  }
}
