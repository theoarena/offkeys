# Microsoft Keyboard for Linux

The app assigns extra keys that Linux does not deliver, starting with My Favorites on the Microsoft Wireless Keyboard 2000.

[What the app does](#what-the-app-does) • [Who it's for](#who-its-for) • [Use the app](#use-the-app) • [Install](#install)

## What the app does

On Linux, some extra keys never reach the desktop. **Microsoft Keyboard** assigns them, starting with My Favorites on the Microsoft Wireless Keyboard 2000.

Typing and the pointer are not mapped here.

Open it from your app grid. Choose the keyboard, pick a key for that keyboard, choose what it should do, and apply. The key works immediately and after the next login.

## Who it's for

You use a keyboard on a Linux desktop, such as GNOME or Zorin, and you want an extra key to open an app, run a command, or trigger a shortcut. The Wireless Keyboard 2000 is the default.

## Use the app

The window is titled **Microsoft Keyboard**.

1. Choose the keyboard. The keys on the screen belong to that keyboard. A keyboard you have not set up yet starts with none, unless it is the Wireless Keyboard 2000 (My Favorites are already there).
2. Pick a listed key, or add one by pressing it. The window asks you to press the extra key, and **Cancel** stops listening.
3. Each key shows what it does. Choose **Open app**, **System shortcut**, **Command**, or **Nothing**. Application names are the ones already on this computer.
4. Select **Apply**.

The key works immediately and after the next login. Closing the window with changes you have not applied asks you to keep editing or discard them.


| Action          | What you get                                                                                                   |
| --------------- | -------------------------------------------------------------------------------------------------------------- |
| Open app        | The key opens an app you already have.                                                                        |
| System shortcut | The key sends a function key from **F13** to **F24**, so your desktop can record it.                          |
| Command         | The key runs a command you type.                                                                               |
| Nothing         | The key stays quiet.                                                                                           |


**Restart mapper**, in the window menu, reloads your keys without changing what you saved. Use it when a key does not respond.

> [!NOTE]
> The app starts your keys when you sign in



## Install

You do this once. After that, open **Microsoft Keyboard** from your apps.

```bash
git clone https://github.com/theoarena/ms-keyboard-linux.git
cd ms-keyboard-linux
sudo python3 mskb.py install
```

Unplug the keyboard and plug it back in once, so the new hidraw permission applies.

On Ubuntu 24.04 or Zorin 18, install the window toolkit if the app does not open. The window needs libadwaita 1.5:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```
