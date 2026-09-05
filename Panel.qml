import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// SoundTap popup: on/off, volume, pack chips, mouse clicks, ignored apps,
// latency bars. Hosted by BarWidget.qml through a Loader; the bar identifies
// the popout by hostWidget (see weather panel for the same shape).
Panel {
  id: root
  moduleName: "io.github.ddm.soundtap"
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
  readonly property var packs: service && service.packs.length ? service.packs : ["cherry-blue", "cherry-brown", "topre", "typewrite"]

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function latencyColor(ms) {
    if (ms < 6) return "#6fb36f"
    if (ms < 15) return "#d1b356"
    return "#b85c5c"
  }

  // Round-trip pings feed the chart while the panel is open, so it moves even
  // when the user is not typing. Key events from the daemon also land here.
  Timer {
    interval: 1000
    repeat: true
    running: root.opened && root.live
    triggeredOnStart: true
    onTriggered: root.service.ping()
  }

  PopupCard {
    id: popup
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.barIdentity
    open: root.opened
    contentWidth: popup.fittedContentWidth(Style.space(300))
    contentHeight: popup.fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      anchors.fill: parent
      spacing: Style.space(10)

      // ---- 1. status switch ----
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
            text: "SOUNDTAP"
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
              : root.service.suppressed ? "muted · " + root.service.activeApp
              : root.service.enabled ? "typing sounds on" : "typing sounds off"
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

      PanelSeparator { foreground: root.fg }

      // ---- 2. volume ----
      Column {
        width: parent.width
        spacing: Style.space(4)

        Item {
          width: parent.width
          height: volLabel.implicitHeight
          PanelSectionHeader { id: volLabel; text: "VOLUME"; foreground: root.fg; fontFamily: root.mono }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: (root.service ? root.service.volume : 0) + "%"
            color: root.fg
            font.family: root.mono
            font.pixelSize: Style.font.caption
          }
        }

        PanelSlider {
          bar: root.bar
          width: parent.width
          minimum: 0
          maximum: 100
          step: 1
          integer: true
          value: root.service ? root.service.volume : 0
          enabled: !!root.service
          onMoved: function(v) { if (root.service) root.service.setVolume(v) }
          onReleased: function(v) { if (root.service) root.service.preview(30) }
        }
      }

      // ---- 3. packs ----
      Column {
        width: parent.width
        spacing: Style.space(6)
        PanelSectionHeader { text: "PACK"; foreground: root.fg; fontFamily: root.mono }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: root.packs
            Button {
              required property var modelData
              readonly property bool isCurrent: root.service && root.service.currentPack === String(modelData)
              text: String(modelData)
              fontFamily: root.mono
              fontSize: Style.font.caption
              foreground: root.fg
              selected: isCurrent
              bordered: true
              horizontalPadding: Style.space(8)
              verticalPadding: Style.space(3)
              opacity: isCurrent ? 1.0 : 0.6
              onClicked: {
                if (!root.service) return
                root.service.setPack(String(modelData))
                previewTimer.restart()
              }
            }
          }

          Button {
            text: "get more packs"
            iconText: "󰏌"
            fontFamily: root.mono
            fontSize: Style.font.caption
            foreground: root.dimFg
            horizontalPadding: Style.space(8)
            verticalPadding: Style.space(3)
            onClicked: if (root.bar) root.bar.run("xdg-open https://github.com/ddm/soundtap#sound-packs")
          }
        }
      }

      // Let the daemon finish loading the new pack before the sample preview.
      Timer {
        id: previewTimer
        interval: 120
        onTriggered: if (root.service) root.service.preview(30)
      }

      PanelSeparator { foreground: root.fg }

      // ---- 4. mouse clicks ----
      Item {
        width: parent.width
        height: Math.max(mouseLabel.implicitHeight, mouseSwitch.implicitHeight)
        Column {
          id: mouseLabel
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)
          Text {
            textFormat: Text.PlainText
            text: "Mouse clicks"
            color: root.fg
            font.family: root.mono
            font.pixelSize: Style.font.body
          }
          Text {
            textFormat: Text.PlainText
            text: "sound on button press"
            color: root.dimFg
            font.family: root.mono
            font.pixelSize: Style.font.caption
          }
        }
        ToggleSwitch {
          id: mouseSwitch
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          foreground: root.fg
          trackHeight: 18
          checked: root.service ? root.service.mouseEnabled : false
          onToggled: if (root.service) root.service.setMouseEnabled(!root.service.mouseEnabled)
        }
      }

      // ---- 5. ignore list ----
      Column {
        width: parent.width
        spacing: Style.space(4)
        PanelSectionHeader { text: "IGNORE THESE APPS"; foreground: root.fg; fontFamily: root.mono }

        TextField {
          id: denyField
          width: parent.width
          foreground: root.fg
          font.family: root.mono
          font.pixelSize: Style.font.caption
          placeholderText: "app ids, comma separated (e.g. kitty, com.mitchellh.ghostty)"
          text: root.service ? root.service.denylistText() : ""
          onEditingFinished: if (root.service) root.service.setDenylistText(text)
          Keys.onEscapePressed: root.close()
        }

        Text {
          textFormat: Text.PlainText
          visible: root.service && root.service.activeApp !== ""
          text: "focused now: " + (root.service ? root.service.activeApp : "")
          color: root.dimFg
          font.family: root.mono
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          width: parent.width
        }
      }

      PanelSeparator { foreground: root.fg }

      // ---- 6. latency ----
      Column {
        width: parent.width
        spacing: Style.space(4)

        Item {
          width: parent.width
          height: latLabel.implicitHeight
          PanelSectionHeader { id: latLabel; text: "LATENCY"; foreground: root.fg; fontFamily: root.mono }
          Text {
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: root.service && root.service.latencies.length
              ? root.service.lastLatency.toFixed(1) + " ms · avg " + root.service.avgLatency.toFixed(1)
              : "—"
            color: root.service && root.service.latencies.length ? root.latencyColor(root.service.lastLatency) : root.dimFg
            font.family: root.mono
            font.pixelSize: Style.font.caption
          }
        }

        // 50 slots, 1px bars with 1px gaps at Style scale; newest at the right.
        Row {
          id: chart
          width: parent.width
          height: Style.space(28)
          spacing: Math.max(1, Math.floor((width - 50 * barW) / 49))
          readonly property int barW: Math.max(1, Math.floor(width / 50) - 1)
          readonly property var data: root.service ? root.service.latencies : []
          readonly property real ceiling: 30

          Repeater {
            model: 50
            Rectangle {
              required property int index
              readonly property int di: index - (50 - chart.data.length)
              readonly property real ms: di >= 0 ? chart.data[di] : -1
              width: chart.barW
              y: chart.height - height
              height: ms < 0 ? 1 : Math.max(2, Math.round(chart.height * Math.min(1, ms / chart.ceiling)))
              radius: 0
              color: ms < 0 ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12) : root.latencyColor(ms)
              Behavior on height { NumberAnimation { duration: 90 } }
            }
          }
        }
      }
    }
  }
}
