import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Omaclack popup. Hosted by BarWidget.qml through a Loader; the bar identifies
// the popout by hostWidget (see the weather panel for the same shape).
// One page: a live keycap that dips on every sounded press, keyboard packs
// grouped by switch family, mouse, quiet. Controls are stock qs.Ui pieces so
// the card reads like the shell's own panels.
Panel {
  id: root
  moduleName: "io.github.diogo7dias.omaclack"
  ipcTarget: ""
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  readonly property var barIdentity: hostWidget || root

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dimFg: Qt.darker(fg, 1.45)
  readonly property string mono: bar ? bar.fontFamily : Style.font.family
  readonly property bool live: service && service.connected
  readonly property bool on: service ? service.enabled : false
  readonly property bool mouseOn: service ? service.mouseEnabled : false
  readonly property var packs: service ? service.packs : []
  readonly property var mousePacks: service ? service.mousePacks : []

  // The list is whatever the daemon found at startup, so a pack added since
  // then is missing until something asks again. Opening the panel is that ask;
  // "rescan" stays for a pack replaced in place, which also has to drop the
  // samples already decoded.
  onOpenedChanged: if (opened && service) service.pollPacks()

  // Keyboard packs by switch family, in a fixed order; packs without a kind
  // (imports, mostly) land in "yours".
  readonly property var kindOrder: ["clicky", "tactile", "linear", "buckling spring"]
  readonly property var packGroups: {
    var groups = [], byKind = {}
    for (var i = 0; i < packs.length; i++) {
      var k = kindOrder.indexOf(packs[i].kind) >= 0 ? packs[i].kind : "yours"
      if (!byKind[k]) byKind[k] = []
      byKind[k].push(packs[i])
    }
    var order = kindOrder.concat(["yours"])
    for (var j = 0; j < order.length; j++) if (byKind[order[j]]) groups.push({ kind: order[j], packs: byKind[order[j]] })
    return groups
  }
  // Brand words repeat on every chip; the tooltip keeps the full name.
  function shortName(p) { return p.name.replace(/^(Cherry|Everglide) /, "") + (p.user ? " ·" : "") }

  readonly property string statusText: !service ? "starting"
    : !live ? "daemon offline"
    : service.inputDenied ? "silent · no /dev/input access"
    : service.meetingMuted ? "muted · microphone in use"
    : service.suppressed ? "muted · " + service.activeApp
    : !service.enabled ? "off"
    : service.inQuietHours ? "quiet hours · " + service.quietPercent + "% volume"
    : service.currentPackMeta ? service.currentPackMeta.name : "on"

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // ---- live activity ----
  // Each sounded press (no keycode reaches the shell) dips the keycap and
  // feeds a scrolling level strip. Ticks only while open and not flat.
  readonly property int stripBars: 40
  property var levels: []
  property real energy: 0
  Connections {
    target: root.service
    function onKeyPressed() {
      if (!root.opened) return
      root.energy = Math.min(1.6, root.energy + 0.75)
      keycap.hit()
      stripTick.start()
    }
  }
  Timer {
    id: stripTick
    interval: 45
    repeat: true
    running: false
    onTriggered: {
      var next = root.levels.slice(Math.max(0, root.levels.length - root.stripBars + 1))
      next.push(Math.min(1, root.energy))
      root.levels = next
      root.energy *= 0.55
      var flat = root.energy < 0.01
      for (var i = 0; flat && i < next.length; i++) if (next[i] > 0.01) flat = false
      if (flat || !root.opened) { stop(); root.levels = [] }
    }
  }

  // Section title with an optional value on the right ("KEYBOARD   70%").
  component SectionLabel: Item {
    property string text: ""
    property string rightText: ""
    width: parent ? parent.width : 0
    height: lbl.implicitHeight
    PanelSectionHeader { id: lbl; text: parent.text; foreground: root.fg; fontFamily: root.mono }
    Text {
      anchors.right: parent.right
      anchors.baseline: lbl.baseline
      textFormat: Text.PlainText
      text: parent.rightText
      color: root.dimFg
      font.family: root.mono
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }

  // Bordered chip that stays lit while `current`.
  component Chip: Button {
    property bool current: false
    fontFamily: root.mono
    fontSize: Style.font.caption
    foreground: root.fg
    selected: current
    bordered: true
    horizontalPadding: Style.space(8)
    verticalPadding: Style.space(3)
  }

  // Title + caption on the left, switch on the right.
  component SwitchRow: Item {
    property string title: ""
    property string subtitle: ""
    property bool checked: false
    signal toggled()
    width: parent ? parent.width : 0
    height: Math.max(col.implicitHeight, sw.implicitHeight)
    Column {
      id: col
      anchors.left: parent.left
      anchors.right: sw.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(1)
      Text { textFormat: Text.PlainText; text: parent.parent.title; color: root.fg; font.family: root.mono; font.pixelSize: Style.font.body }
      Text {
        textFormat: Text.PlainText; text: parent.parent.subtitle; color: root.dimFg
        font.family: root.mono; font.pixelSize: Style.font.caption; visible: text !== ""
        width: parent.width; elide: Text.ElideRight
      }
    }
    ToggleSwitch {
      id: sw
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      foreground: root.fg
      trackHeight: 18
      checked: parent.checked
      onToggled: parent.toggled()
    }
  }

  // A wrapped run of pack chips; picking one plays a short typed phrase.
  component PackChips: Flow {
    property var list: []
    property string current: ""
    signal picked(string id)
    spacing: Style.space(5)
    Repeater {
      model: parent.list
      Chip {
        required property var modelData
        text: root.shortName(modelData)
        tooltipText: modelData.name + (modelData.credit ? " · " + modelData.credit : "")
        current: modelData.id === parent.current
        onClicked: parent.picked(modelData.id)
      }
    }
  }

  // Keycap drawn in hairlines: a skirt, and a cap that dips and springs back.
  component LiveKeycap: Item {
    id: cap
    property real travel: 0          // 0 = at rest, 1 = bottomed out
    readonly property real lift: Style.space(5)
    implicitWidth: Style.space(44)
    implicitHeight: Style.space(44)

    function hit() { press.restart(); ring.restart() }

    SequentialAnimation {
      id: press
      NumberAnimation { target: cap; property: "travel"; to: 1; duration: 28; easing.type: Easing.OutQuad }
      NumberAnimation { target: cap; property: "travel"; to: 0; duration: 260; easing.type: Easing.OutBack; easing.overshoot: 2.6 }
    }

    // Ring that spreads from the cap on each hit.
    Rectangle {
      id: ripple
      anchors.centerIn: skirt
      width: skirt.width; height: skirt.height
      radius: Style.cornerRadius
      color: "transparent"
      border.color: root.fg
      border.width: 1
      opacity: 0
      ParallelAnimation {
        id: ring
        NumberAnimation { target: ripple; property: "scale"; from: 1.0; to: 1.55; duration: 420; easing.type: Easing.OutCubic }
        NumberAnimation { target: ripple; property: "opacity"; from: 0.55; to: 0; duration: 420; easing.type: Easing.OutCubic }
      }
    }

    Rectangle {
      id: skirt
      anchors.bottom: parent.bottom
      anchors.horizontalCenter: parent.horizontalCenter
      width: parent.width
      height: parent.height - cap.lift
      radius: Style.cornerRadius
      color: Util.alpha(root.fg, 0.05)
      border.color: Util.alpha(root.fg, 0.35)
      border.width: 1
    }

    Rectangle {
      id: top
      width: skirt.width - Style.space(10)
      height: skirt.height - Style.space(10)
      anchors.horizontalCenter: parent.horizontalCenter
      y: Style.space(1) + cap.travel * (cap.lift + Style.space(3))
      radius: Style.cornerRadius
      color: Util.alpha(root.fg, 0.10 + cap.travel * 0.18)
      border.color: root.fg
      border.width: 1
      Text {
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: "󰌓"
        color: root.fg
        opacity: 0.8 + cap.travel * 0.2
        font.family: root.mono
        font.pixelSize: Style.font.display
      }
    }
  }

  PopupCard {
    id: popup
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.barIdentity
    open: root.opened
    contentWidth: popup.fittedContentWidth(Style.space(360))
    contentHeight: popup.fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      width: parent.width
      spacing: Style.space(12)

      // ---- hero ----
      PanelHero {
        width: parent.width
        title: "Omaclack"
        meta: root.statusText
        foreground: root.service && root.service.inputDenied ? Color.urgent : root.fg
        fontFamily: root.mono
        iconOpacity: root.on && root.live ? 1.0 : 0.5
        iconComponent: Component { LiveKeycap { id: keycapItem; Component.onCompleted: keycap.target = keycapItem } }
        trailingControl: Component {
          ToggleSwitch {
            foreground: root.fg
            checked: root.on
            onToggled: if (root.service) root.service.toggle()
          }
        }
      }
      QtObject {
        id: keycap
        property var target: null
        function hit() { if (target) target.hit() }
      }

      // Scrolling level strip: newest press on the right.
      Item {
        width: parent.width
        height: Style.space(18)
        Row {
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          spacing: Style.space(2)
          Repeater {
            model: root.stripBars
            Rectangle {
              required property int index
              readonly property real v: {
                var i = index - (root.stripBars - root.levels.length)
                return i >= 0 ? root.levels[i] : 0
              }
              anchors.bottom: parent.bottom
              width: Math.max(2, (column.width - (root.stripBars - 1) * Style.space(2)) / root.stripBars)
              height: Math.max(1, v * Style.space(18))
              color: root.fg
              opacity: 0.18 + v * 0.82
            }
          }
        }
      }

      // Why nothing plays, and the fix, when the daemon cannot read keys.
      Text {
        visible: !!root.service && root.service.inputDenied
        width: parent.width
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        text: "This session cannot read /dev/input. Join the input group (sudo usermod -aG input $USER), then log out and back in."
        color: Color.urgent
        font.family: root.mono
        font.pixelSize: Style.font.caption
      }

      // ---- keyboard ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        opacity: root.on ? 1 : 0.55

        SectionLabel { text: "KEYBOARD"; rightText: (root.service ? root.service.volume : 0) + "%" }
        PanelSlider {
          bar: root.bar; width: parent.width; minimum: 0; maximum: 100; step: 1; integer: true
          value: root.service ? root.service.volume : 0
          enabled: !!root.service
          onMoved: function(v) { if (root.service) root.service.setVolume(v) }
          onReleased: function(v) { if (root.service) root.service.preview(30) }
        }
        Repeater {
          model: root.packGroups
          Item {
            required property var modelData
            width: parent.width
            height: Math.max(kindLabel.implicitHeight, chips.implicitHeight)
            Text {
              id: kindLabel
              width: Style.space(70)
              y: Style.space(4)
              textFormat: Text.PlainText
              text: parent.modelData.kind.toUpperCase()
              color: root.dimFg
              font.family: root.mono
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1
              wrapMode: Text.WordWrap
            }
            PackChips {
              id: chips
              anchors.left: kindLabel.right
              anchors.right: parent.right
              list: parent.modelData.packs
              current: root.service ? root.service.currentPack : ""
              onPicked: function(id) { if (!root.service) return; root.service.setPack(id); root.service.audition() }
            }
          }
        }
        SwitchRow {
          title: "Velocity"
          subtitle: "louder when you type fast"
          checked: root.service ? root.service.velocity : false
          onToggled: if (root.service) root.service.setVelocity(!root.service.velocity)
        }
      }

      PanelSeparator { foreground: root.fg }

      // ---- mouse ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        opacity: root.on ? 1 : 0.55

        Item {
          width: parent.width
          height: Math.max(mouseLabel.height, mouseSwitch.implicitHeight)
          SectionLabel {
            id: mouseLabel
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - mouseSwitch.width - Style.space(10)
            text: "MOUSE"
            rightText: root.mouseOn ? (root.service ? root.service.mouseVolume : 0) + "%" : "off"
          }
          ToggleSwitch {
            id: mouseSwitch
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            foreground: root.fg
            trackHeight: 18
            checked: root.mouseOn
            onToggled: if (root.service) root.service.setMouseEnabled(!root.service.mouseEnabled)
          }
        }
        PanelSlider {
          visible: root.mouseOn
          bar: root.bar; width: parent.width; minimum: 0; maximum: 100; step: 1; integer: true
          value: root.service ? root.service.mouseVolume : 0
          enabled: !!root.service
          onMoved: function(v) { if (root.service) root.service.setMouseVolume(v) }
          onReleased: function(v) { if (root.service) root.service.preview(272) }
        }
        PackChips {
          visible: root.mouseOn
          width: parent.width
          list: root.mousePacks
          current: root.service ? root.service.mousePack : ""
          onPicked: function(id) { if (!root.service) return; root.service.setMousePack(id); root.service.preview(272) }
        }
      }

      PanelSeparator { foreground: root.fg }

      // ---- quiet ----
      Column {
        width: parent.width
        spacing: Style.space(8)

        SectionLabel { text: "QUIET" }
        SwitchRow {
          title: "Mute in calls"
          subtitle: root.service && root.service.micInUse ? "microphone in use right now" : "while any app records the microphone"
          checked: root.service ? root.service.muteInMeetings : false
          onToggled: if (root.service) root.service.setMuteInMeetings(!root.service.muteInMeetings)
        }
        SwitchRow {
          title: "Quiet hours"
          subtitle: root.service ? root.service.quietFrom + " – " + root.service.quietTo + " at " + root.service.quietPercent + "%" : ""
          checked: root.service ? root.service.quietHours : false
          onToggled: if (root.service) root.service.setQuietHours(!root.service.quietHours)
        }
        Row {
          visible: root.service ? root.service.quietHours : false
          width: parent.width
          spacing: Style.space(6)
          TextField {
            id: fromField; width: Style.space(64); foreground: root.fg; font.family: root.mono; font.pixelSize: Style.font.caption
            text: root.service ? root.service.quietFrom : ""
            onEditingFinished: if (root.service) root.service.setQuietRange(text, toField.text)
          }
          Text { anchors.verticalCenter: parent.verticalCenter; text: "to"; color: root.dimFg; font.family: root.mono; font.pixelSize: Style.font.caption }
          TextField {
            id: toField; width: Style.space(64); foreground: root.fg; font.family: root.mono; font.pixelSize: Style.font.caption
            text: root.service ? root.service.quietTo : ""
            onEditingFinished: if (root.service) root.service.setQuietRange(fromField.text, text)
          }
          PanelSlider {
            anchors.verticalCenter: parent.verticalCenter
            bar: root.bar; width: parent.width - fromField.width - toField.width - Style.space(40)
            minimum: 0; maximum: 100; step: 5; integer: true
            value: root.service ? root.service.quietPercent : 0
            onMoved: function(v) { if (root.service) root.service.setQuietPercent(v) }
          }
        }
        TextField {
          width: parent.width
          foreground: root.fg
          font.family: root.mono
          font.pixelSize: Style.font.caption
          placeholderText: root.service && root.service.activeApp !== "" ? "ignore apps, e.g. " + root.service.activeApp : "ignore apps by id, comma separated"
          text: root.service ? root.service.denylistText() : ""
          onEditingFinished: if (root.service) root.service.setDenylistText(text)
          Keys.onEscapePressed: root.close()
        }
      }

      PanelSeparator { foreground: root.fg }

      // ---- footer ----
      Item {
        width: parent.width
        height: Math.max(credit.implicitHeight, footerRow.implicitHeight)
        Text {
          id: credit
          anchors.left: parent.left
          anchors.right: footerRow.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: root.service && root.service.currentPackMeta ? "sounds: " + root.service.currentPackMeta.credit : ""
          color: root.dimFg
          font.family: root.mono
          font.pixelSize: Style.font.caption
          font.underline: creditHover.hovered
          elide: Text.ElideRight
          HoverHandler { id: creditHover; cursorShape: Qt.PointingHandCursor }
          TapHandler {
            onTapped: if (root.bar && root.service && root.service.currentPackMeta && root.service.currentPackMeta.source)
              root.bar.run("xdg-open " + root.service.currentPackMeta.source)
          }
        }
        Row {
          id: footerRow
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(6)
          Chip {
            text: "packs folder"
            tooltipText: "Your own packs live here; import with tools/omaclack-import"
            onClicked: if (root.bar && root.service) root.bar.run("mkdir -p '" + root.service.userPacksDir + "' && xdg-open '" + root.service.userPacksDir + "'")
          }
          Chip {
            text: "rescan"
            onClicked: if (root.service) root.service.refreshPacks()
          }
        }
      }
    }
  }
}
