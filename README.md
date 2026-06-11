# rsync GUI

Graphical frontend for rsync with live progress, option checkboxes, and desktop notifications.

## Requirements

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-notify-0.7 rsync
```

## Run

```bash
python3 rsync_gui.py
```

## Features

- Source (file or folder) and destination selection
- 14 rsync options with descriptions
- Live command preview
- Progress bar with speed and remaining time
- Desktop notifications on completion/error/cancel
- Copy command to clipboard
- Cancel running transfers
- i18n: Deutsch / English / System (auto-detect)
- Config: `~/.config/rsync-gui/config.json`

## Language

Language can be switched via the dropdown at the top. "System" auto-detects from locale. Restart required after switching.

## License

MIT — NoCoderGHG
