# kaihen-switch1-titles

hbmenu from your Switch 1 games on a Switch 2 running kaiHEN. No Switch 2 game needed, no PC step.

**The Seedsow Lullaby demo on its own is now enough:** start kaiHEN with it, then hold R on Seedsow
itself (or any Switch 1 game you have) to get hbmenu. You don't need to own a Switch 2 game.

> [!WARNING]
> This builds on kaiHEN, which gives homebrew full access to the system, NAND included. A bad
> homebrew or a mistake can brick a console for good. Use it at your own risk, on a console and
> games you own. See [Legal](#legal) before you use it or open an issue.

## Why

A lot of people start kaiHEN with Seedsow, a Switch 1 demo, and then find that holding R does
nothing unless they own a Switch 2 game. Stock HEN only takes over native Switch 2 games, so an
R-held launch of Seedsow or any other Switch 1 game just starts the game. I wanted hbmenu from the
games I actually have.

This is a change to kaiHEN's HEN that adds Switch 1 games:

- Hold R while starting a Switch 1 game and hbmenu opens inside that game's process.
- It does not need a Switch 2 game, ever. A Switch 1 game, Seedsow included, can be the first R
  launch after a fresh kaiHEN install.
- The first time, HEN builds the package it needs (a "donor": hbloader plus that game's own code
  files) from the game itself, while the game runs normally. Every R launch after that, of that
  game or another Switch 1 game, opens hbmenu.
- Switch 2 games work as they always did.

It is proposed upstream as [kaiHEN #18](https://github.com/ouncebrew/kaihen/pull/18) (Switch 1
games, no Switch 2 game needed, shared donor) and
[kaiHEN #19](https://github.com/ouncebrew/kaihen/pull/19) (HEN builds the donor itself). Once they
are merged, use kaiHEN's own release instead of anything here.

## Using it

You need a Switch 2 that can start kaiHEN, for example with
[seedsow-fs-kaihen](https://github.com/Malev0/seedsow-fs-kaihen). I tested on firmware 23.0.1.

1. Build HEN from the branch behind the two pull requests, with the ounce devkit (see kaiHEN's
   README for the build environment):

   ```sh
   git clone -b switch1-auto-donor https://github.com/ignotusbucius/kaihen
   cd kaihen
   docker build --output out .
   ```

   `out/hen/main` is the HEN. `patches/kaihen-switch1-titles.patch` in this repo is the same change
   as one diff against kaiHEN's `main` at `9aa3716`, if you prefer to apply it yourself.

2. Put it where your launcher stages kaiHEN from, so it ends up on the SD card as
   `/kaihen/bin/hen/main`. For seedsow-fs-kaihen that is `kaihen/hen/main` in the kit's folder
   (its "Update kaiHEN" button replaces that file, so keep a copy).

3. Start kaiHEN as usual. Close Seedsow, then hold R and start any Switch 1 game. The first time,
   the game starts normally while HEN writes `/kaihen/bin/s1/default.nsp` (about 15 seconds).
   Close it.

4. Hold R and start a Switch 1 game again: hbmenu.

`default.nsp` stays on the SD card, so step 3 happens once, not once per session. Delete it to make
HEN build a new one from the next Switch 1 game you R-launch.

A game can also have its own package at `/kaihen/bin/s1/<program ID>.nsp`, which HEN prefers over
`default.nsp`. `tools/mkdonor.py` builds one on a PC from an ExeFS you dumped from your own copy of
the game (`python3 tools/mkdonor.py <dir named after the program ID> --hbloader <kaiHEN's game/main
or release zip> --no-hen-log`). You do not need this unless a game does not work with the shared
package.

## Tested

Switch 2, firmware 23.0.1, kaiHEN started by seedsow-fs-kaihen's console-only chain. Each line held
on its own console boot; "own ID" means a homebrew run from that hbmenu reported the game's
program ID as its own.

| What | Result |
|---|---|
| No Switch 2 game launched, no saved policy: Seedsow with its own package | hbmenu, own ID |
| Shared package built from Seedsow: Worms Armageddon, GTA III DE, Moving Out | hbmenu, own ID each |
| HEN builds the shared package from GTA III, then Worms and Moving Out | built package byte-identical to `mkdonor.py`'s; hbmenu, own ID each |
| HEN builds it from Worms on another boot, then GTA III and Moving Out | byte-identical again; hbmenu |
| Heave Ho 2 (Switch 2) on a fresh install | first R launch runs normally and saves its policy, second opens hbmenu, as in stock kaiHEN |

Not tested yet: 32-bit Switch 1 games (HEN refuses to build the shared package from one, and they
are not expected to work with a 64-bit one), other firmware, and what an outdated package does after
a game update. Reports for other games are welcome.

## How it works

Loader starts a Switch 1 game from a code mount with the game's Switch 1 program ID and content
attributes 0; stock HEN only arms content attributes 1 and Switch 2 IDs, so it never matched.
Loader also loads every module of a Switch 1 game (`rtld`, `main`, `subsdk*`, `sdk`) from that
mount, so the package HEN swaps in has to look like the game: hbloader in the `rtld` slot, which is
the module the process starts in, and the game's own files for the rest. Loader reads the launched
game's own `main.npdm` before the swap and only loads modules after it, so a package built from one
Switch 1 game works for others.

## Safety

- Everything HEN does here is in RAM except its writes to the SD card: the donor package (written
  as `default.nsp.part` and renamed only when complete) and kaiHEN's own policy file. Nothing is
  written to NAND, system files or game saves.
- Every build was reviewed and host-tested before it ran, and a result counted only once it held on
  separate console boots.
- The console stayed offline and never took a system update.

## Limitations

- The package contains game code. It is built on your console from your copy of the game; do not
  share it.
- Homebrew runs with the Switch 1 game's permissions, not a native game's.
- After a game update, delete that game's package (or `default.nsp` if it was built from that game)
  and let HEN build a new one.
- You still need a way to start kaiHEN. This only changes what an R-held launch does once it runs.

## Legal

- This project is not affiliated with, endorsed by or sponsored by Nintendo. Nintendo Switch and
  Nintendo Switch 2 are trademarks of Nintendo. Game titles are trademarks of their owners and are
  named here only to say what was tested.
- The repo contains no Nintendo code, firmware, keys or game files, and nothing in it downloads
  any. The donor package is built on your own console from your own installed game.
- It does not help anyone run a game they do not own. It changes what an R-held launch of a game
  already installed on your console does, and what it starts is homebrew, not a game.
- Please do not attach donor packages, ExeFS files, keys, NAND or save dumps, or other copyrighted
  files to issues.
- Homebrew on a console that goes online can get the console banned from Nintendo's online
  services. Keep it offline.
- No warranty. The software is provided as is; see sections 11 and 12 of the [LICENSE](LICENSE).

## Credits

- [kaiHEN](https://github.com/ouncebrew/kaihen) by kurumuz and the kaiHEN contributors: HEN and the
  R-held launch this extends. The donor layout follows its `writeExeFsPackage`.
- [ouncebrew](https://github.com/ouncebrew): the ounce devkit, libounce and OunceShell.
- Malev0 for [seedsow-fs-kaihen](https://github.com/Malev0/seedsow-fs-kaihen); alula for the
  Seedsow save and [switch-artemis-launcher](https://github.com/alula/switch-artemis-launcher);
  DialingSpoon527 for [seedsow-lua-editor](https://github.com/DialingSpoon527/seedsow-lua-editor).
- [switchbrew](https://switchbrew.org): the wiki, libnx, nx-hbloader and nx-hbmenu.
- Atmosphère, for the Switch 1 application ID bounds.

## Licence

The patch changes GPL-2.0-only files of kaiHEN and is GPL-2.0-only; its new files carry SPDX
headers. It is offered on kaiHEN's own terms, including the Nintendo exemption in kaiHEN's README,
so it can go upstream as is. `tools/mkdonor.py` and its tests are GPL-2.0-only too. See
[LICENSE](LICENSE).
