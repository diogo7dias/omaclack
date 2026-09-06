import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Omaclack popup. Hosted by BarWidget.qml through a Loader; the bar identifies
// the popout by hostWidget (see the weather panel for the same shape).
// Four pages (Keys / Mouse / Quiet / More) so the card fits a 1200px screen
// without scrolling.
Panel {
  id: root
  moduleName: "io.github.diogo7dias.omaclack"
  ipcTarget: ""
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  readonly property var barIdentity: hostWidget || root
  property int tabIndex: 0   // 0 Keys, 1 Mouse, 2 Quiet, 3 More

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dimFg: Qt.darker(fg, 1.45)
  readonly property string mono: bar ? bar.fontFamily : Style.font.family
  readonly property bool live: service && service.connected
  readonly property var packs: service ? service.packs : []
  readonly property var packMeta: service ? service.currentPackMeta : null
  readonly property var mousePacks: service ? service.mousePacks : []
  readonly property var mousePackMeta: service ? service.mousePackMeta : null
  readonly property var rooms: service ? [{ id: "none", name: "Dry" }].concat(service.rooms) : []
  readonly property var hint: service ? service.deviceHint : null
  readonly property bool mouseOn: service ? service.mouseEnabled : false

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function latencyColor(ms) {
    if (ms < 6) return "#6fb36f"
    if (ms < 15) return "#d1b356"
    return "#b85c5c"
  }

  readonly property var keyNames: ({
    1: "esc", 14: "bksp", 15: "tab", 28: "enter", 29: "ctrl", 42: "shift", 54: "shift", 56: "alt", 57: "space",
    58: "caps", 97: "ctrl", 100: "altgr", 102: "home", 103: "up", 105: "left", 106: "right", 107: "end",
    108: "down", 111: "del", 125: "super", 16: "q", 17: "w", 18: "e", 19: "r", 20: "t", 21: "y", 22: "u",
    23: "i", 24: "o", 25: "p", 30: "a", 31: "s", 32: "d", 33: "f", 34: "g", 35: "h", 36: "j", 37: "k",
    38: "l", 44: "z", 45: "x", 46: "c", 47: "v", 48: "b", 49: "n", 50: "m", 51: ",", 52: ".", 53: "/",
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0", 12: "-", 13: "=",
    26: "[", 27: "]", 39: ";", 40: "'", 41: "`", 43: "\\"
  })
  function keyName(code) { return keyNames[code] || ("#" + code) }

  // Round-trip pings feed the latency chart and stats refresh while More is open.
  Timer {
    interval: 1000
    repeat: true
    running: root.opened && root.live && root.tabIndex === 3
    triggeredOnStart: true
    onTriggered: { root.service.ping(); root.service.requestStats() }
  }

  component SectionLabel: Item {
    property string text: ""
    property string rightText: ""
    width: parent ? parent.width : 0
    height: lbl.implicitHeight
    PanelSectionHeader { id: lbl; text: parent.text; foreground: root.fg; fontFamily: root.mono }
    Text {
      anchors.right: parent.right
      textFormat: Text.PlainText
      text: parent.rightText
      color: root.fg
      font.family: root.mono
      font.pixelSize: Style.font.caption
    }
  }

  component Chip: Button {
    property bool current: false
    fontFamily: root.mono
    fontSize: Style.font.caption
    foreground: root.fg
    selected: current
    bordered: true
    horizontalPadding: Style.space(7)
    verticalPadding: Style.space(2)
    opacity: current ? 1.0 : 0.6
  }

  component Switch: Item {
    property string title: ""
    property string subtitle: ""
    property bool checked: false
    property real trackH: 18
    signal toggled()
    width: parent ? parent.width : 0
    height: Math.max(col.implicitHeight, sw.implicitHeight)
    Column {
      id: col
      anchors.left: parent.left
      anchors.right: sw.left
      anchors.rightMargin: Style.space(8)
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
      trackHeight: parent.trackH
      checked: parent.checked
      onToggled: parent.toggled()
    }
  }

  component Credit: Text {
    property var meta: null
    visible: !!meta && meta.credit !== ""
    width: parent ? parent.width : 0
    textFormat: Text.PlainText
    text: meta ? "sounds: " + meta.credit + (meta.release === "derived" ? " · release derived" : "") : ""
    color: root.dimFg
    font.family: root.mono
    font.pixelSize: Style.font.caption
    font.underline: hh.hovered
    elide: Text.ElideRight
    HoverHandler { id: hh; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: if (root.bar && parent.meta && parent.meta.source) root.bar.run("xdg-open " + parent.meta.source) }
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
      spacing: Style.space(10)

      // ---- status (always) ----
      Item {
        width: parent.width
        height: Math.max(statusText.implicitHeight, statusSwitch.implicitHeight)
        Column {
          id: statusText
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)
          Text {
            textFormat: Text.PlainText
            text: "OMACLACK"
            color: root.fg
            font.family: root.mono
            font.pixelSize: Style.font.subtitle
            font.bold: true
            font.letterSpacing: 1.2
          }
          Text {
            textFormat: Text.PlainText
            text: !root.service ? "starting service"
              : !root.live ? "daemon offline"
              : root.service.inputDenied ? "no /dev/input access · see README"
              : root.service.meetingMuted ? "muted · microphone in use"
              : root.service.suppressed ? "muted · " + root.service.activeApp
              : !root.service.enabled ? "typing sounds off"
              : root.service.inQuietHours ? "quiet hours · " + root.service.quietPercent + "% volume"
              : "typing sounds on"
            color: root.service && root.service.inputDenied ? Color.urgent : root.dimFg
            font.family: root.mono
            font.pixelSize: Style.font.caption
          }
        }
        ToggleSwitch {
          id: statusSwitch
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          foreground: root.fg
          checked: root.service ? root.service.enabled : false
          onToggled: if (root.service) root.service.toggle()
        }
      }

      Flow {
        width: parent.width
        spacing: Style.space(5)
        Chip { current: root.tabIndex === 0; text: "Keys"; onClicked: root.tabIndex = 0 }
        Chip { current: root.tabIndex === 1; text: "Mouse"; onClicked: root.tabIndex = 1 }
        Chip { current: root.tabIndex === 2; text: "Quiet"; onClicked: root.tabIndex = 2 }
        Chip { current: root.tabIndex === 3; text: "More"; onClicked: root.tabIndex = 3 }
      }

      // ---- Keys ----
      Column {
        visible: root.tabIndex === 0
        width: parent.width
        spacing: Style.space(8)

        Column {
          width: parent.width
          spacing: Style.space(4)
          SectionLabel { text: "VOLUME"; rightText: (root.service ? root.service.volume : 0) + "%" }
          PanelSlider {
            bar: root.bar; width: parent.width; minimum: 0; maximum: 100; step: 1; integer: true
            value: root.service ? root.service.volume : 0
            enabled: !!root.service
            onMoved: function(v) { if (root.service) root.service.setVolume(v) }
            onReleased: function(v) { if (root.service) root.service.preview(30) }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(6)
          SectionLabel { text: "PACK"; rightText: root.packs.length + " packs" }
          Flow {
            width: parent.width
            spacing: Style.space(5)
            Repeater {
              model: root.packs
              Chip {
                required property var modelData
                current: root.service && root.service.currentPack === modelData.id
                text: modelData.name + (modelData.user ? " ·" : "")
                onClicked: { if (!root.service) return; root.service.setPack(modelData.id); previewTimer.restart() }
              }
            }
          }
          Credit { meta: root.packMeta }
          Row {
            visible: !!root.hint && root.service && root.service.currentPack !== root.hint.pack
            spacing: Style.space(6)
            width: parent.width
            Text {
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: root.hint ? root.hint.text + ":" : ""
              color: root.dimFg
              font.family: root.mono
              font.pixelSize: Style.font.caption
            }
            Chip {
              text: root.hint && root.service.metaFor(root.packs, root.hint.pack) ? "try " + root.service.metaFor(root.packs, root.hint.pack).name : ""
              onClicked: { if (root.hint) { root.service.setPack(root.hint.pack); previewTimer.restart() } }
            }
          }
          Timer { id: previewTimer; interval: 120; onTriggered: if (root.service) root.service.preview(30) }
        }

        Column {
          width: parent.width
          spacing: Style.space(6)
          SectionLabel { text: "FEEL" }
          Flow {
            width: parent.width
            spacing: Style.space(5)
            Chip {
              text: "Velocity"
              current: root.service ? root.service.velocity : false
              onClicked: if (root.service) root.service.setVelocity(!root.service.velocity)
            }
            Chip {
              text: "Release"
              current: root.service ? root.service.releaseSounds : false
              onClicked: if (root.service) root.service.setReleaseSounds(!root.service.releaseSounds)
            }
            Repeater {
              model: root.rooms
              Chip {
                required property var modelData
                current: root.service && root.service.room === modelData.id
                text: modelData.name
                onClicked: { if (!root.service) return; root.service.setRoom(modelData.id); roomPreview.restart() }
              }
            }
          }
          Timer { id: roomPreview; interval: 600; onTriggered: if (root.service) root.service.preview(30) }
        }
      }

      // ---- Mouse ----
      Column {
        visible: root.tabIndex === 1
        width: parent.width
        spacing: Style.space(6)
        Switch {
          title: "Mouse clicks"; subtitle: "own pack and volume"
          checked: root.mouseOn
          onToggled: if (root.service) root.service.setMouseEnabled(!root.service.mouseEnabled)
        }
        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.mouseOn
          Flow {
            width: parent.width
            spacing: Style.space(5)
            Repeater {
              model: root.mousePacks
              Chip {
                required property var modelData
                current: root.service && root.service.mousePack === modelData.id
                text: modelData.name
                onClicked: { if (!root.service) return; root.service.setMousePack(modelData.id); mousePreview.restart() }
              }
            }
          }
          Column {
            width: parent.width
            spacing: Style.space(4)
            SectionLabel { text: "VOLUME"; rightText: (root.service ? root.service.mouseVolume : 0) + "%" }
            PanelSlider {
              bar: root.bar; width: parent.width; minimum: 0; maximum: 100; step: 1; integer: true
              value: root.service ? root.service.mouseVolume : 0
              enabled: !!root.service
              onMoved: function(v) { if (root.service) root.service.setMouseVolume(v) }
              onReleased: function(v) { if (root.service) root.service.preview(272) }
            }
          }
          Credit { meta: root.mousePackMeta }
          Timer { id: mousePreview; interval: 120; onTriggered: if (root.service) root.service.preview(272) }
        }
      }

      // ---- Quiet ----
      Column {
        visible: root.tabIndex === 2
        width: parent.width
        spacing: Style.space(6)
        Switch {
          title: "Mute in calls"; subtitle: root.service && root.service.micInUse ? "microphone in use right now" : "while any app records the microphone"
          checked: root.service ? root.service.muteInMeetings : false
          onToggled: if (root.service) root.service.setMuteInMeetings(!root.service.muteInMeetings)
        }
        Switch {
          title: "Quiet hours"; subtitle: root.service ? root.service.quietFrom + " – " + root.service.quietTo + " at " + root.service.quietPercent + "%" : ""
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
        Column {
          width: parent.width
          spacing: Style.space(4)
          SectionLabel { text: "IGNORE THESE APPS"; rightText: root.service && root.service.activeApp !== "" ? "focused: " + root.service.activeApp : "" }
          TextField {
            width: parent.width
            foreground: root.fg
            font.family: root.mono
            font.pixelSize: Style.font.caption
            placeholderText: "app ids, comma separated (e.g. kitty, com.mitchellh.ghostty)"
            text: root.service ? root.service.denylistText() : ""
            onEditingFinished: if (root.service) root.service.setDenylistText(text)
            Keys.onEscapePressed: root.close()
          }
        }
      }

      // ---- More ----
      Column {
        id: moreCol
        visible: root.tabIndex === 3
        width: parent.width
        spacing: Style.space(8)

        Column {
          width: parent.width
          spacing: Style.space(4)
          SectionLabel {
            text: "TYPING"
            rightText: root.service ? root.service.stats.kpm + " keys/min · " + root.service.stats.total + " this session" : ""
          }
          Row {
            width: parent.width
            spacing: Style.space(10)
            Row {
              id: rhythm
              readonly property var values: root.service ? root.service.stats.rhythm : []
              readonly property int peak: { var m = 1; for (var i = 0; i < values.length; i++) m = Math.max(m, values[i]); return m }
              height: Style.space(28)
              spacing: Style.space(2)
              Repeater {
                model: 10
                Rectangle {
                  required property int index
                  readonly property int n: rhythm.values.length > index ? rhythm.values[index] : 0
                  width: Style.space(9)
                  y: rhythm.height - height
                  height: Math.max(2, Math.round(rhythm.height * n / rhythm.peak))
                  color: n ? root.fg : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12)
                  opacity: n ? 0.5 + 0.5 * (index === 0 ? 1 : 1 - index / 10) : 1
                  Behavior on height { NumberAnimation { duration: 120 } }
                }
              }
            }
            Column {
              spacing: Style.space(1)
              Text { textFormat: Text.PlainText; text: "rhythm  fast → slow"; color: root.dimFg; font.family: root.mono; font.pixelSize: Style.font.caption }
              Text {
                textFormat: Text.PlainText
                text: {
                  var top = root.service ? root.service.stats.top : []
                  if (!top || !top.length) return "top keys: type something"
                  return "top keys: " + top.map(function(t) { return root.keyName(t[0]) + " " + t[1] }).join("  ")
                }
                color: root.dimFg; font.family: root.mono; font.pixelSize: Style.font.caption
              }
              Text {
                textFormat: Text.PlainText
                text: root.service && root.service.mainDevice ? "keyboard: " + root.service.mainDevice : "keyboard: waiting for a key"
                color: root.dimFg; font.family: root.mono; font.pixelSize: Style.font.caption
                width: moreCol.width - rhythm.width - Style.space(10); elide: Text.ElideRight
              }
            }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(4)
          SectionLabel {
            text: "LATENCY"
            rightText: root.service && root.service.latencies.length
              ? root.service.lastLatency.toFixed(1) + " ms · avg " + root.service.avgLatency.toFixed(1) : "—"
          }
          Row {
            id: chart
            width: parent.width
            height: Style.space(20)
            spacing: Math.max(1, Math.floor((width - 50 * barW) / 49))
            readonly property int barW: Math.max(1, Math.floor(width / 50) - 1)
            readonly property var values: root.service ? root.service.latencies : []
            readonly property real ceiling: 30
            Repeater {
              model: 50
              Rectangle {
                required property int index
                readonly property int di: index - (50 - chart.values.length)
                readonly property real ms: di >= 0 ? chart.values[di] : -1
                width: chart.barW
                y: chart.height - height
                height: ms < 0 ? 1 : Math.max(2, Math.round(chart.height * Math.min(1, ms / chart.ceiling)))
                color: ms < 0 ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12) : root.latencyColor(ms)
                Behavior on height { NumberAnimation { duration: 90 } }
              }
            }
          }
        }

        Flow {
          width: parent.width
          spacing: Style.space(5)
          Chip {
            text: root.service && root.service.lastTheme
              ? (root.service.themePacks[root.service.lastTheme] === root.service.currentPack
                 ? "theme " + root.service.lastTheme + " → this pack ✓"
                 : "use this pack for theme " + root.service.lastTheme)
              : "theme hook not installed"
            onClicked: {
              if (!root.service) return
              if (root.service.lastTheme) {
                var bound = root.service.themePacks[root.service.lastTheme] === root.service.currentPack
                root.service.bindThemePack(root.service.lastTheme, bound ? "" : root.service.currentPack)
              } else if (root.bar) {
                root.bar.run("omarchy hook install theme-set '" + root.service.pluginDir + "tools/omaclack-theme-hook' && omarchy hook theme-set \"$(omarchy theme current 2>/dev/null || echo default)\"")
              }
            }
          }
          Chip {
            text: "packs folder"
            onClicked: if (root.bar && root.service) root.bar.run("mkdir -p '" + root.service.userPacksDir + "' && xdg-open '" + root.service.userPacksDir + "'")
          }
          Chip {
            text: "rescan packs"
            onClicked: if (root.service) root.service.refreshPacks()
          }
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: "import a Mechvibes pack: tools/omaclack-import <folder-or-zip>"
          color: root.dimFg
          font.family: root.mono
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
