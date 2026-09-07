import QtQuick
import qs.Commons
import qs.Ui

// Bar entry: a Nerd Font keyboard glyph, drawn the same way the shell's own
// bar icons are so it matches them in weight and optical size. Left click
// toggles the panel, right click toggles sound on/off, wheel nudges volume.
BarWidget {
  id: root
  moduleName: "io.github.diogo7dias.omaclack"

  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor("io.github.diogo7dias.omaclack") : null
  readonly property bool soundOn: service ? (service.enabled && !service.suppressed && !service.meetingMuted) : false

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
    onStatusChanged: if (status === Loader.Error) console.warn("omaclack: Panel.qml failed to load:", sourceComponent ? sourceComponent.errorString() : "")
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰌓"   // nf-md-keyboard_variant; 󰌐 (keyboard_off) while muted
    dimmed: !root.soundOn
    active: root.opened
    tooltipText: root.service
      ? (root.soundOn
         ? "Omaclack · " + (root.service.currentPackMeta ? root.service.currentPackMeta.name : root.service.currentPack) + " · " + root.service.volume + "%"
         : "Omaclack · off")
      : "Omaclack · starting"

    onPressed: function(b) {
      if (b === Qt.RightButton) { if (root.service) root.service.toggle() }
      else root.togglePanel()
    }
    onWheelMoved: function(delta) {
      if (!root.service) return
      root.service.setVolume(root.service.volume + (delta > 0 ? 5 : -5))
    }
  }
}
