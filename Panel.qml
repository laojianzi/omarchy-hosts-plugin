import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.omarchy.hosts"
  ipcTarget: "hosts"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color barForegroundColor: bar ? bar.barForeground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color hoverFill: bar ? Style.hoverFillFor(bar.foreground, Color.accent) : Style.hoverFill
  readonly property color selectedFill: bar ? Style.selectedFillFor(bar.foreground, Color.accent) : Style.selectedFill
  readonly property real openIndicatorInlineOffset: bar && bar.vertical ? 0 : Style.spaceReal(1.5)

  readonly property string iconGlyph: "󰒍"
  readonly property string glyphAdd: "󰐕"
  readonly property string glyphEdit: "󰏫"
  readonly property string glyphDelete: "󰆴"
  readonly property string glyphRefresh: "󰑐"

  property string mode: "list" // list | form | preview
  property bool cursorActive: false
  property int rowIndex: 0
  readonly property int addRowIndex: svc.profiles.length
  property string pendingDeleteId: ""

  property string draftId: ""
  property string draftName: ""
  property string draftDescription: ""
  property string draftEntries: ""
  property bool draftEnabled: false

  function statusColor() {
    var kind = String(svc.status.kind || "")
    if (kind === "error" || kind === "drift") return root.urgent
    if (kind === "pending") return Color.accent
    if (kind === "synced") return root.foreground
    return root.dim
  }

  function statusGlyph() {
    var kind = String(svc.status.kind || "")
    if (kind === "error") return "✕"
    if (kind === "drift") return "!"
    if (kind === "pending") return "◐"
    if (kind === "synced") return "●"
    return "○"
  }

  function warningText() {
    if (!svc.plan || !svc.plan.warnings || svc.plan.warnings.length === 0) return ""
    var lines = []
    for (var i = 0; i < svc.plan.warnings.length; i++) {
      lines.push("• " + String(svc.plan.warnings[i].message || "Warning"))
    }
    return lines.join("\n")
  }

  function selectedProfile() {
    if (rowIndex < 0 || rowIndex >= svc.profiles.length) return null
    return svc.profiles[rowIndex]
  }

  function clampCursor() {
    var max = svc.profiles.length
    rowIndex = Math.max(0, Math.min(max, rowIndex))
  }

  function setRowCursor(index) {
    cursorActive = true
    rowIndex = index
  }

  function moveCursor(dy) {
    cursorActive = true
    clampCursor()
    rowIndex = Math.max(0, Math.min(svc.profiles.length, rowIndex + dy))
    scrollCursorIntoView()
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item) return
      var margin = Style.space(6)
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin) panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollCursorIntoView() {
    if (rowIndex >= 0 && rowIndex < profileColumn.children.length) scrollItemIntoView(profileColumn.children[rowIndex])
    else if (rowIndex === addRowIndex) scrollItemIntoView(addRow)
  }

  function activateCursor() {
    if (mode === "preview") {
      if (svc.status.canApply && !svc.busy) svc.applyChanges()
      return
    }
    if (mode !== "list") return
    if (rowIndex === addRowIndex) {
      openAddForm()
      return
    }
    var profile = selectedProfile()
    if (profile && !svc.busy) svc.toggleProfile(profile.id, !profile.enabled)
  }

  function openAddForm() {
    draftId = ""
    draftName = ""
    draftDescription = ""
    draftEntries = "127.0.0.1 app.test\n"
    draftEnabled = false
    mode = "form"
    Qt.callLater(function() { if (nameField) nameField.forceActiveFocus() })
  }

  function openEditForm(profile) {
    if (!profile) return
    draftId = String(profile.id)
    draftName = String(profile.name || "")
    draftDescription = String(profile.description || "")
    draftEntries = String(profile.entriesText || "")
    draftEnabled = profile.enabled === true
    mode = "form"
    Qt.callLater(function() { if (nameField) nameField.forceActiveFocus() })
  }

  function closeSubpage() {
    mode = "list"
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function submitForm() {
    if (svc.busy) return
    svc.saveProfile({
      id: draftId,
      name: draftName,
      description: draftDescription,
      entriesText: draftEntries,
      enabled: draftEnabled
    })
  }

  function openPreview() {
    mode = "preview"
    if (panelFlick) panelFlick.contentY = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function requestDelete(profile) {
    if (!profile || svc.busy) return
    pendingDeleteId = String(profile.id)
    confirmDialog.message = "Delete profile \"" + String(profile.name) + "\"?"
    confirmDialog.selectedIndex = 0
    confirmDialog.opened = true
    Qt.callLater(function() { confirmDialog.forceActiveFocus() })
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    mode = "list"
    cursorActive = false
    pendingDeleteId = ""
    confirmDialog.opened = false
    if (panelFlick) panelFlick.contentY = 0
    clampCursor()
    svc.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service { id: svc }

  Connections {
    target: svc
    function onChanged() { root.clampCursor() }
    function onOperationSucceeded(action, data) {
      if (action === "save" || action === "apply" || action === "undo") root.closeSubpage()
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { return svc.refresh() ? "started" : "busy" }
    function status(): string { return JSON.stringify({ status: svc.status, summary: svc.summary, helper: svc.helper }) }
    function list(): string { return JSON.stringify(svc.profiles) }
    function enable(ref: string): string {
      var profile = svc.profileByRef(ref)
      if (!profile) return "unknown"
      return svc.toggleProfile(profile.id, true) ? "started" : "busy"
    }
    function disable(ref: string): string {
      var profile = svc.profileByRef(ref)
      if (!profile) return "unknown"
      return svc.toggleProfile(profile.id, false) ? "started" : "busy"
    }
    function apply(): string {
      if (!svc.status.canApply) return "not-ready"
      return svc.applyChanges() ? "started" : "busy"
    }
    function undo(): string {
      if (!svc.status.canUndo) return "not-ready"
      return svc.undo() ? "started" : "busy"
    }
  }

  Item {
    id: button
    anchors.fill: parent
    implicitWidth: root.bar && root.bar.vertical ? root.bar.barSize : Style.space(27)
    implicitHeight: root.bar && root.bar.vertical ? Style.space(26) : (root.bar ? root.bar.barSize : Style.space(26))

    property var registeredBar: null

    function syncClickRegistration() {
      if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(button)
      registeredBar = root.bar
      if (registeredBar && registeredBar.registerClickTarget) registeredBar.registerClickTarget(button)
    }

    Component.onCompleted: syncClickRegistration()
    Component.onDestruction: if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(button)

    Connections {
      target: root
      function onBarChanged() { button.syncClickRegistration() }
    }

    Text {
      anchors.centerIn: parent
      anchors.horizontalCenterOffset: root.openIndicatorInlineOffset
      text: root.iconGlyph
      font.family: root.fontFamily
      font.pixelSize: Style.font.icon
      color: root.statusColor()
      opacity: svc.busy ? 0.55 : 1.0

      SequentialAnimation on opacity {
        running: svc.busy
        loops: Animation.Infinite
        NumberAnimation { to: 0.28; duration: 550; easing.type: Easing.InOutQuad }
        NumberAnimation { to: 1.0; duration: 550; easing.type: Easing.InOutQuad }
      }
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.MiddleButton
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: function(mouse) {
        if (mouse.button === Qt.MiddleButton) svc.refresh()
        else root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(480))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.mode === "form" || confirmDialog.opened

      onMoveRequested: function(dx, dy) {
        if (root.mode !== "list" || dy === 0) return
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dy)
      }
      onActivateRequested: root.activateCursor()
      onCloseRequested: {
        if (root.mode !== "list") root.closeSubpage()
        else root.close()
      }
      onDeleteRequested: {
        if (root.mode !== "list") return
        root.cursorActive = true
        var profile = root.selectedProfile()
        if (profile) root.requestDelete(profile)
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (root.mode === "preview") {
          if ((t === "a" || t === "A") && svc.status.canApply) svc.applyChanges()
          return
        }
        if (t === "a" || t === "A") root.openAddForm()
        else if (t === "e" || t === "E") {
          root.cursorActive = true
          var p = root.selectedProfile()
          if (p) root.openEditForm(p)
        }
        else if (t === "r" || t === "R") svc.refresh()
        else if (t === "p" || t === "P") root.openPreview()
        else if ((t === "u" || t === "U") && svc.status.canUndo) svc.undo()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: QQC2.ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "Hosts"
            meta: svc.summary.enabledProfileCount + " enabled · " + svc.summary.configuredEntryCount + " entries"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: root.iconGlyph
                color: root.statusColor()
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            visible: svc.busy || svc.notice !== "" || svc.error !== ""
            width: parent.width
            text: svc.busy
              ? (svc.busyAction === "apply" || svc.busyAction === "undo" ? "Waiting for authorization…" : "Working…")
              : (svc.error !== "" ? svc.error : svc.notice)
            textFormat: Text.PlainText
            color: svc.error !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          CursorSurface {
            width: parent.width
            foreground: root.foreground
            fill: root.hoverFill
            current: svc.status.kind === "synced"
            currentFill: root.selectedFill
            implicitHeight: statusContent.implicitHeight + Style.spacing.rowPaddingX

            RowLayout {
              id: statusContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(9)

              Text {
                text: root.statusGlyph()
                color: root.statusColor()
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                Layout.alignment: Qt.AlignVCenter
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  Layout.fillWidth: true
                  text: String(svc.status.label || "Loading…")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  Layout.fillWidth: true
                  visible: svc.status.error !== null && svc.status.error !== undefined
                  text: svc.status.error ? String(svc.status.error.message || "") : ""
                  textFormat: Text.PlainText
                  color: root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }

              PanelActionButton {
                iconText: root.glyphRefresh
                tooltipText: "Refresh"
                foreground: root.foreground
                fontFamily: root.fontFamily
                Layout.alignment: Qt.AlignVCenter
                onClicked: svc.refresh()
              }
            }
          }

          CursorSurface {
            visible: svc.loaded && !svc.helper.ready
            width: parent.width
            foreground: root.foreground
            implicitHeight: helperText.implicitHeight + Style.spacing.xl

            Text {
              id: helperText
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(10)
              text: "System helper is not installed or is insecure. Review the package, then run:\ncd " + svc.pluginDir + "/packaging/arch && makepkg -si"
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WrapAnywhere
            }
          }

          Column {
            visible: root.mode === "list"
            width: parent.width
            spacing: Style.space(10)

            PanelSeparator { foreground: root.foreground }

            PanelSectionHeader {
              text: "PROFILES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              visible: svc.loaded && svc.profiles.length === 0
              width: parent.width
              text: "No profiles yet. Add a profile to group development, VPN, lab, or client mappings."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }

            Column {
              id: profileColumn
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: svc.profiles
                ProfileRow {
                  required property var modelData
                  required property int index
                  width: profileColumn.width
                  profile: modelData
                  rowIdx: index
                }
              }
            }

            CursorSurface {
              id: addRow
              width: parent.width
              hasCursor: root.cursorActive && root.rowIndex === root.addRowIndex
              foreground: root.foreground
              fill: root.hoverFill
              implicitHeight: addInner.implicitHeight + Style.spacing.xl

              Row {
                id: addInner
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(8)
                spacing: Style.space(8)

                Text {
                  text: root.glyphAdd
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.icon
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  text: "Add profile"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: root.setRowCursor(root.addRowIndex)
                onClicked: root.openAddForm()
              }
            }

            Text {
              visible: svc.plan && svc.plan.warnings && svc.plan.warnings.length > 0
              width: parent.width
              text: svc.plan && svc.plan.warnings
                ? svc.plan.warnings.length + " non-blocking warning(s); review the diff for details."
                : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Row {
              width: parent.width
              layoutDirection: Qt.RightToLeft
              spacing: Style.space(10)

              Button {
                text: "Review changes"
                bordered: true
                focusable: true
                enabled: !!svc.plan && svc.plan.changed === true && !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.openPreview()
              }

              Button {
                text: "Undo last apply"
                bordered: true
                focusable: true
                visible: svc.status.canUndo === true
                enabled: !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: svc.undo()
              }
            }
          }

          Column {
            visible: root.mode === "preview"
            width: parent.width
            spacing: Style.space(10)

            PanelSeparator { foreground: root.foreground }
            PanelSectionHeader {
              text: "REVIEW /ETC/HOSTS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              text: svc.plan && svc.plan.changed
                ? "Only the marked Omarchy Hosts block will change. Every unmanaged byte is preserved."
                : "There are no pending changes."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            QQC2.ScrollView {
              width: parent.width
              height: Style.space(310)
              clip: true

              QQC2.TextArea {
                id: diffArea
                readOnly: true
                text: svc.plan && svc.plan.diff ? String(svc.plan.diff) : "No changes"
                color: root.foreground
                selectionColor: Color.accent
                selectedTextColor: Color.background
                font.family: "monospace"
                font.pixelSize: Style.font.caption
                wrapMode: TextEdit.NoWrap
                selectByMouse: true
                leftPadding: Style.space(10)
                rightPadding: Style.space(10)
                topPadding: Style.space(8)
                bottomPadding: Style.space(8)
                background: Rectangle {
                  color: "transparent"
                  radius: Style.space(5)
                  border.color: root.dim
                  border.width: 1
                }
              }
            }

            Text {
              visible: root.warningText() !== ""
              width: parent.width
              text: root.warningText()
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Row {
              width: parent.width
              layoutDirection: Qt.RightToLeft
              spacing: Style.space(10)

              Button {
                text: "Authenticate & apply"
                bordered: true
                focusable: true
                enabled: svc.status.canApply === true && !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: svc.applyChanges()
              }
              Button {
                text: "Back"
                bordered: true
                focusable: true
                enabled: !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.closeSubpage()
              }
            }
          }

          Column {
            visible: root.mode === "form"
            width: parent.width
            spacing: Style.space(11)

            PanelSeparator { foreground: root.foreground }
            PanelSectionHeader {
              text: root.draftId === "" ? "NEW PROFILE" : "EDIT PROFILE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            FormField {
              width: parent.width
              label: "Name"
              TextField {
                id: nameField
                width: parent.width
                foreground: root.foreground
                placeholderText: "e.g. Local development"
                text: root.draftName
                onTextChanged: root.draftName = text
                Keys.onEscapePressed: root.closeSubpage()
              }
            }

            FormField {
              width: parent.width
              label: "Description (optional)"
              TextField {
                width: parent.width
                foreground: root.foreground
                placeholderText: "What this profile is for"
                text: root.draftDescription
                onTextChanged: root.draftDescription = text
                Keys.onEscapePressed: root.closeSubpage()
              }
            }

            FormField {
              width: parent.width
              label: "Entries — one 'IP hostname [alias …]' mapping per line"

              QQC2.ScrollView {
                width: parent.width
                height: Style.space(190)
                clip: true

                QQC2.TextArea {
                  id: entriesArea
                  text: root.draftEntries
                  placeholderText: "127.0.0.1 app.test api.app.test\n10.0.0.8 service.internal"
                  color: root.foreground
                  selectionColor: Color.accent
                  selectedTextColor: Color.background
                  font.family: "monospace"
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: TextEdit.NoWrap
                  selectByMouse: true
                  leftPadding: Style.space(10)
                  rightPadding: Style.space(10)
                  topPadding: Style.space(8)
                  bottomPadding: Style.space(8)
                  onTextChanged: root.draftEntries = text
                  Keys.onEscapePressed: root.closeSubpage()
                  background: Rectangle {
                    color: "transparent"
                    radius: Style.space(5)
                    border.color: root.dim
                    border.width: 1
                  }
                }
              }
            }

            Text {
              width: parent.width
              text: "Blank lines and # comments are ignored. IPv4, IPv6, aliases, IDN names, and local names containing underscores are supported. /etc/hosts does not support wildcards."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Toggle {
              width: parent.width
              label: "Enabled"
              description: "Enabled profiles are included in the next reviewed apply."
              checked: root.draftEnabled
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.draftEnabled = !root.draftEnabled
            }

            Row {
              width: parent.width
              layoutDirection: Qt.RightToLeft
              spacing: Style.space(10)

              Button {
                text: "Save"
                bordered: true
                focusable: true
                enabled: !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.submitForm()
              }
              Button {
                text: "Cancel"
                bordered: true
                focusable: true
                enabled: !svc.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.closeSubpage()
              }
            }
          }
        }
      }
    }

    ConfirmDialog {
      id: confirmDialog
      anchors.fill: parent
      confirmText: "Delete"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onConfirmed: {
        if (root.pendingDeleteId !== "") svc.deleteProfile(root.pendingDeleteId)
        root.pendingDeleteId = ""
        opened = false
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      }
      onCanceled: {
        root.pendingDeleteId = ""
        opened = false
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      }
      Keys.onPressed: function(event) { if (handleKey(event)) event.accepted = true }
      focus: opened
    }
  }

  component FormField: Column {
    property string label: ""
    default property alias content: holder.children
    spacing: Style.space(4)

    Text {
      text: parent.label
      visible: text !== ""
      color: Qt.darker(root.foreground, 1.4)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Item {
      id: holder
      width: parent.width
      implicitHeight: childrenRect.height
    }
  }

  component ProfileRow: CursorSurface {
    id: profileRow
    property var profile: null
    property int rowIdx: 0
    readonly property bool isEnabled: profile && profile.enabled === true

    hasCursor: root.cursorActive && root.mode === "list" && root.rowIndex === rowIdx
    current: isEnabled
    foreground: root.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: Math.max(rowContent.implicitHeight, Style.space(42)) + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) root.setRowCursor(profileRow.rowIdx)
      onClicked: if (profileRow.profile && !svc.busy) svc.toggleProfile(profileRow.profile.id, !profileRow.isEnabled)
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(8)

      Text {
        text: profileRow.isEnabled ? "●" : "○"
        color: profileRow.isEnabled ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rowContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          Layout.fillWidth: true
          text: profileRow.profile ? String(profileRow.profile.name) : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: profileRow.isEnabled
          elide: Text.ElideRight
        }

        Text {
          Layout.fillWidth: true
          text: {
            if (!profileRow.profile) return ""
            var count = Number(profileRow.profile.entryCount || 0)
            var description = String(profileRow.profile.description || "")
            return count + (count === 1 ? " entry" : " entries") + (description !== "" ? " · " + description : "")
          }
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        iconText: root.glyphEdit
        tooltipText: "Edit"
        foreground: root.foreground
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.openEditForm(profileRow.profile)
      }

      PanelActionButton {
        iconText: root.glyphDelete
        tooltipText: "Delete"
        foreground: root.foreground
        hoverColor: root.urgent
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.requestDelete(profileRow.profile)
      }
    }
  }
}
