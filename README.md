# OffKeys

Linux app to bind extra keyboard keys to shortcuts, apps, and commands.

[What the app does](#what-the-app-does) • [Who it's for](#who-its-for) • [Use the app](#use-the-app) • [Install](#install) • [Tested devices](#tested-devices) • [Tested systems](#tested-systems)

## What the app does

On Linux, some extra keys never reach the desktop. **OffKeys** assigns them: open an app, send a system shortcut, or run a command.

Typing and the pointer are not mapped here.

Open it from your app grid. Choose the keyboard, pick a key for that keyboard, choose what it should do, and apply. 

The "Microsoft Wireless Keyboard 2000" is the default. Other extra-key devices can be learned; they stay off [Tested devices](#tested-devices) until someone actually tests them.

## Who it's for

You use a keyboard on a Linux desktop and you want an extra key to open an app, run a command, or trigger a shortcut.

## Use the app

1. Open the app.
2. Choose your keyboard from the list. The keys on the screen belong to that keyboard. A keyboard you have not set up yet starts with none, unless it is the "Wireless Keyboard 2000" (Default).
3. Pick a listed key, or add one by pressing it. The window asks you to press the extra key, and **Cancel** stops listening.
4. Each key shows what it does. Choose **Open app**, **System shortcut**, **Command**, or **Nothing**.
5. Select **Apply**.

The key works immediately and in future sessions.


| Action          | What you get                                                                                                   |
| --------------- | -------------------------------------------------------------------------------------------------------------- |
| Open app        | The key opens an app you choose.                                                                        |
| System shortcut | The key sends a function key from **F13** to **F24**, so your desktop can record it.                          |
| Command         | The key runs a command you type.                                                                               |
| Nothing         | The key stays quiet.                                                                                           |


**Restart mapper**, in the window menu, reloads your keys without changing what you saved. Use it when a key does not respond.

## Install

Do this once. After that, open **OffKeys** from your apps.

```bash
git clone https://github.com/theoarena/offkeys.git
cd offkeys
sudo python3 mskb.py install
```

Unplug the keyboard and plug it back in once, so the new hidraw permission applies.

On Ubuntu 24.04, install the window toolkit if the app does not open. The window needs libadwaita 1.5:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

## Tested devices

Only devices we have used ourselves. Other extra-key HID devices can be learned in the app; they stay off this list until tested.

| Device | VID:PID | Notes |
| --- | --- | --- |
| Microsoft Wireless Keyboard 2000 | `045e:0745` | Default. My Favorites already named. |

## Tested systems

Developed and tested on Ubuntu-based desktops. Not tested on Fedora, Arch, or other families.

| Distro | Desktop | Notes |
| --- | --- | --- |
| Ubuntu 24.04 | GNOME | Developed here. Needs libadwaita 1.5. |
| Zorin 18 | GNOME | Tested. |
