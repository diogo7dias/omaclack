import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Omaclack popup. Hosted by BarWidget.qml through a Loader; the bar identifies
// the popout by hostWidget (see the weather panel for the same shape).
// One page: hero, keyboard, mouse, quiet. Every control is a stock qs.Ui
// piece so the card reads like the shell's own panels.
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
  readonly property var hint: service ? service.deviceHint : null

  function options(list) {
    var out = []
    for (var i = 0; i < list.length; i++) out.push({ value: list[i].id, label: list[i].name + (list[i].user ? " ·" : "") })
    return out
  }
  readonly property var packOptions: options(packs)
  readonly property var mousePackOptions: options(mousePacks)
  readonly property var roomOptions: {
    var out = [{ value: "none", label: "No room" }]
    var rooms = service ? service.rooms : []
    for (var i = 0; i < rooms.length; i++) out.push({ value: rooms[i].id, label: rooms[i].name })
    return out
  }

  readonly property string statusText: !service ? "starting"
    : !live ? "daemon offline"
    : service.inputDenied ? "no /dev/input access · see README"
    : service.meetingMuted ? "muted · microphone in use"
    : service.suppressed ? "muted · " + service.activeApp
    : !service.enabled ? "off"
    : service.inQuietHours ? "quiet hours · " + service.quietPercent + "% volume"
    : service.currentPackMeta ? service.currentPackMeta.name : "on"

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  Timer { id: keyPreview; interval: 120; onTriggered: if (root.service) root.service.preview(30) }
  Timer { id: roomPreview; interval: 600; onTriggered: if (root.service) root.service.preview(30) }
  Timer { id: mousePreview; interval: 120; onTriggered: if (root.service) root.service.preview(272) }

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

  component PackDropdown: Dropdown {
    width: parent ? parent.width : implicitWidth
    showLabel: false
    foreground: root.fg
    fontFamily: root.mono
  }

  PopupCard {
    id: popup
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.barIdentity
    open: root.opened
    contentWidth: popup.fittedContentWidth(Style.space(340))
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
        iconComponent: Component {
          Text {
            textFormat: Text.PlainText
            text: "󰌓"
            color: root.fg
            font.family: root.mono
            font.pixelSize: Style.font.display
          }
        }
        trailingControl: Component {
          ToggleSwitch {
            foreground: root.fg
            checked: root.on
            onToggled: if (root.service) root.service.toggle()
          }
        }
      }

      PanelSeparator { foreground: root.fg }

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
        PackDropdown {
          options: root.packOptions
          value: root.service ? root.service.currentPack : ""
          onChanged: function(v) { if (!root.service) return; root.service.setPack(v); keyPreview.restart() }
        }
        Row {
          width: parent.width
          spacing: Style.space(6)
          Chip {
            id: velocityChip
            text: "Velocity"
            tooltipText: "Louder when you type fast"
            current: root.service ? root.service.velocity : false
            onClicked: if (root.service) root.service.setVelocity(!root.service.velocity)
          }
          Chip {
            id: releaseChip
            text: "Release"
            tooltipText: "Play key-up sounds"
            current: root.service ? root.service.releaseSounds : false
            onClicked: if (root.service) root.service.setReleaseSounds(!root.service.releaseSounds)
          }
          PackDropdown {
            width: parent.width - velocityChip.width - releaseChip.width - Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            options: root.roomOptions
            value: root.service ? root.service.room : "none"
            onChanged: function(v) { if (!root.service) return; root.service.setRoom(v); roomPreview.restart() }
          }
        }
        Row {
          visible: !!root.hint && root.service && root.service.currentPack !== root.hint.pack
          spacing: Style.space(6)
          width: parent.width
          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: root.hint ? root.hint.text : ""
            color: root.dimFg
            font.family: root.mono
            font.pixelSize: Style.font.caption
          }
          Chip {
            text: root.hint && root.service.metaFor(root.packs, root.hint.pack) ? "try " + root.service.metaFor(root.packs, root.hint.pack).name : ""
            onClicked: { if (root.hint) { root.service.setPack(root.hint.pack); keyPreview.restart() } }
          }
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
        PackDropdown {
          visible: root.mouseOn
          options: root.mousePackOptions
          value: root.service ? root.service.mousePack : ""
          onChanged: function(v) { if (!root.service) return; root.service.setMousePack(v); mousePreview.restart() }
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
