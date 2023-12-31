⚠️  This integration is in developement, expect entities to disappear/change.

![Radio France logo](https://brands.home-assistant.io/radio_france/logo.png)

# Radio-France for home-assistant

Component to display information about currently airing emissions on Radio France stations based RadioFrance api. Thanks to them for making this available 💌.

## Installation

It must be used as a custom repository via hacs.

## Configuration

Once the custom integration has been added, add `radio_france` integration through the UI.

You need an api key, see https://developers.radiofrance.fr/doc for details.

## Exposed sensors

At the moment, this integration exposes 3 entities per station:
- "Airing now": exposing the currently aired program (like a show).
- "Current track" exposing the currently aired music, if any.
- a calendar exposing the recent past and planned program + tracks.

## Known issue

FIP (and other music-only stations) is a special case and thus is not [supported yet](https://github.com/kamaradclimber/radio-france-home-assistant/issues/1).
