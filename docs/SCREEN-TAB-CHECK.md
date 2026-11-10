# Pyxi in the tab on screen (no extension): measured

Written by `scripts/screen_tab_check.py` on 2026-11-09 21:56. 3 round(s) per browser; every "confirmed" is the page's own report of its state.

Browsers: chrome 156.0.8078.80, firefox 159.0.1, edge 157.0.4341.42.

Each cell is found / acted / confirmed (times out of times tried). "F" after a cell: the
browser window took the keyboard focus that many times.

## By kind of action

| Kind of action | chrome | firefox | edge |
|---|---|---|---|
| read | 15/15 / 15/15 / 15/15 | 15/15 / 15/15 / 15/15 | 15/15 / 15/15 / 15/15 |
| refuse | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| button | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 |
| clickable div | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| link | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| other events | 6/9 / 6/6 / 6/6 | 6/9 / 6/6 / 3/6 | 6/9 / 6/6 / 6/6 |
| text field | 18/18 / 18/18 / 18/18 | 18/18 / 18/18 / 18/18 | 18/18 / 18/18 / 18/18 |
| controlled input | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| rich text box | 6/6 / 0/6 / 0/6 | 3/6 / 3/3 / 3/3 | 6/6 / 0/6 / 0/6 |
| checkbox | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 |
| radio button | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| dropdown | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 |
| custom dropdown | 6/6 / 6/6 / 6/6 F1 | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 |
| single-page app | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| tied to what was read | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 |
| iframe | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 |
| modal dialog | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 | 6/6 / 6/6 / 6/6 |
| shadow DOM | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 | 9/9 / 9/9 / 9/9 |
| scroll | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 |
| keys (window in front) | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 | 12/12 / 12/12 / 12/12 |
| back | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | 45/45 / 45/45 / 45/45 | 45/45 / 45/45 / 45/45 | 45/45 / 45/45 / 45/45 |
| takes the keyboard? (UI Automation patterns first) | 14/14 / 14/14 / 4/14 F10 | 0/14 / 0/14 / 0/14 | 0/14 / 0/14 / 0/14 |
| takes the keyboard? (older default action first) | 14/14 / 14/14 / 12/14 F2 | 0/14 / 0/14 / 0/14 | 0/14 / 0/14 / 0/14 |
| window showing, not in front | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| window covered by another window | 3/3 / 3/3 / 3/3 F1 | 1/1 / 0/1 / 0/1 | 3/3 / 3/3 / 3/3 |
| window minimised | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 |
| window read while showing, then covered | 2/2 / 2/2 / 2/2 | 2/2 / 0/2 / 0/2 | 2/2 / 2/2 / 2/2 |
| window read while showing, then minimised | 2/2 / 0/2 / 0/2 | 2/2 / 0/2 / 0/2 | 2/2 / 0/2 / 0/2 |
| new tab | 2/2 / 2/2 / 2/2 F1 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 |
| open an address | 2/2 / 2/2 / 2/2 F1 | 2/2 / 2/2 / 2/2 | 2/2 / 2/2 / 2/2 F1 |
| public page | 7/7 / 7/7 / 7/7 F3 | 7/7 / 7/7 / 7/7 F1 | 7/7 / 7/7 / 7/7 F3 |
| private window | 2/2 / 2/2 / 2/2 | 1/1 / 1/1 / 1/1 | 2/2 / 2/2 / 2/2 |

## Every case

| Kind | Case | chrome | firefox | edge |
|---|---|---|---|---|
| read | text, nothing hidden, card number masked | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| read | outline: kinds and labels | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| read | a form's button is marked as submitting | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| refuse | nothing outside the page is listed (toolbar, address bar) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| read | a long page: text past the screen too | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| read | after scrolling to the bottom, the look starts from there | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| button | Add Milk (a plain button) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| button | Preview (type=button in a form) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| button | Join now (a form's submit button) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| button | Bottom button, far off screen | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| clickable div | Show opening hours (a div with a listener) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| link | Go to the sign-up form | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| other events | a button that only hears mouse down | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| other events | a button that only hears pointer down | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 0/3 | 3/3 / 3/3 / 3/3 |
| other events | a menu that only shows on hover | 0/3 / - / - | 0/3 / - / - | 0/3 / - / - |
| text field | Full name | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| text field | Email (type=email) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| text field | Message (a textarea) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| text field | How many (type=number) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| text field | Search groceries (type=search) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| text field | the page hears input and change events | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| controlled input | React-style field (state only from real input events) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| rich text box | contenteditable with role=textbox (a mail body) | 3/3 / 0/3 / 0/3 | 3/3 / 3/3 / 3/3 | 3/3 / 0/3 / 0/3 |
| rich text box | a bare contenteditable div | 3/3 / 0/3 / 0/3 | 0/3 / - / - | 3/3 / 0/3 / 0/3 |
| checkbox | Send me the newsletter | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| checkbox | Add candles, and the page hears change | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| radio button | Large | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| dropdown | Country: pick Canada (value) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| dropdown | Flavour: pick Lemon, and the page hears change | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| custom dropdown | made of divs: open it, pick Bangladesh | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| custom dropdown | ARIA combobox: open it, pick Large | 3/3 / 3/3 / 3/3 F1 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| single-page app | two steps, same address | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| tied to what was read | an element the page removed is refused | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| tied to what was read | a button whose words changed is refused | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| tied to what was read | an action for the page before this one is refused | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| iframe | same site: type in the frame's field | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| iframe | same site: press the frame's button | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| iframe | another site's frame: left out of the page, its text not read | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| modal dialog | <dialog>: open, type, save | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| modal dialog | an overlay made of divs: open, say yes | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| shadow DOM | open shadow tree: type in its field | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| shadow DOM | open shadow tree: press its button | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| shadow DOM | closed shadow tree: press its button | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| scroll | down a page | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| scroll | to the bottom | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| scroll | to the bottom, then back to the top | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| scroll | bring a named element into view | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| keys (window in front) | Enter in a search box | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 |
| keys (window in front) | Enter in a form's field submits it | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 |
| keys (window in front) | Escape closes an overlay | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 | 4/4 / 4/4 / 4/4 |
| back | follow a link, then go back a page | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Password: type=password | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Card number: its label, and autocomplete=cc-number | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Social insurance number: its label | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Photo: a file picker | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Passwort: labelled by other text, in German | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Kartennummer: its label, in German | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Choose one: type=password, no label | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Login: only its name= says password | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Number: only its name= says card | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Code: only its name= says one-time code | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | 3 digits: only its name= says security code | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Box 1: one of four boxes for a card number, no label | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | after Show password it is still the password | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Nickname, a labelled field beside password fields, is typed into | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| private field | Business name is NOT private (has 'ssn' inside it) | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 | 3/3 / 3/3 / 3/3 |
| takes the keyboard? (UI Automation patterns first) | reading the page | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | pressing a button | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | typing in a field | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | ticking a box | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | picking from a dropdown | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | bringing an element into view | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (UI Automation patterns first) | following a link | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | reading the page | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | pressing a button | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | typing in a field | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | ticking a box | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | picking from a dropdown | 2/2 / 2/2 / 0/2 F2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | bringing an element into view | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| takes the keyboard? (older default action first) | following a link | 2/2 / 2/2 / 2/2 | 0/2 / 0/2 / 0/2 | 0/2 / 0/2 / 0/2 |
| window showing, not in front | read a page loaded in that state | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| window showing, not in front | press a button | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| window showing, not in front | type in a field | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| window covered by another window | read a page loaded in that state | 1/1 / 1/1 / 1/1 | 1/1 / 0/1 / 0/1 | 1/1 / 1/1 / 1/1 |
| window covered by another window | press a button | 1/1 / 1/1 / 1/1 F1 | not run | 1/1 / 1/1 / 1/1 |
| window covered by another window | type in a field | 1/1 / 1/1 / 1/1 | not run | 1/1 / 1/1 / 1/1 |
| window minimised | read a page loaded in that state | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 |
| window read while showing, then covered | (what the page says of itself) | - / - / - | - / - / - | - / - / - |
| window read while showing, then covered | press a button | 1/1 / 1/1 / 1/1 | 1/1 / 0/1 / 0/1 | 1/1 / 1/1 / 1/1 |
| window read while showing, then covered | type in a field | 1/1 / 1/1 / 1/1 | 1/1 / 0/1 / 0/1 | 1/1 / 1/1 / 1/1 |
| window read while showing, then minimised | (what the page says of itself) | - / - / - | - / - / - | - / - / - |
| window read while showing, then minimised | press a button | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 |
| window read while showing, then minimised | type in a field | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 | 1/1 / 0/1 / 0/1 |
| new tab | a link that opens a new tab: Pyxi carries on there | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| new tab | and the tab now showing is the one that is read | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| open an address | handed to the browser's own program, opens as a new tab | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 F1 |
| open an address | one that sends the browser on to another host: the real page is reported | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| public page | Wikipedia: read the main page | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| public page | Wikipedia: search for Ada Lovelace (its Search button) | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 F1 |
| public page | Wikipedia: follow a link in the article | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 F1 |
| public page | Wikipedia: go back a page | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| public page | Python docs: read the front page | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| public page | Python docs: follow a navigation link | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 F1 | 1/1 / 1/1 / 1/1 F1 |
| public page | MDN: read a reference page | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| private window | not picked, not read, nothing opened from it | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 | 1/1 / 1/1 / 1/1 |
| private window | not picked, not read, nothing opened from it (display language: ja) | 1/1 / 1/1 / 1/1 | not run | 1/1 / 1/1 / 1/1 |

## Notes from the runs (failures, and what the page said)

- chrome, other events, a menu that only shows on hover: not in the outline
- chrome, rich text box, contenteditable with role=textbox (a mail body): failed: The words were put in that box, but it is a rich text box and the page was not told, so it will most likely ignore them. That's a rich text box (the kind a mail body or a chat message is written in). Without Pyxi's browser extension its text can't be set in a way the page notices. Don't try again: say what is left for them, and that the extension can do it (Settings, Connect your browser).
- chrome, rich text box, a bare contenteditable div: failed: That's a rich text box (the kind a mail body or a chat message is written in). Without Pyxi's browser extension its text can't be set in a way the page notices. Don't try again: say what is left for them, and that the extension can do it (Settings, Connect your browser).
- chrome, window showing, not in front, read a page loaded in that state: the page says it is visible
- chrome, window covered by another window, read a page loaded in that state: the page says it is visible
- chrome, window minimised, read a page loaded in that state: the page says it is hidden; failed: The page isn't answering. Without the browser extension the browser wi
- chrome, window read while showing, then covered, (what the page says of itself): the page says it is visible
- chrome, window read while showing, then minimised, (what the page says of itself): the page says it is hidden
- chrome, window read while showing, then minimised, press a button: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- chrome, window read while showing, then minimised, type in a field: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- chrome, public page, Wikipedia: read the main page: 8688 characters, 200 elements, more
- chrome, public page, Wikipedia: search for Ada Lovelace (its Search button): https://en.wikipedia.org/wiki/Ada_Lovelace
- chrome, public page, Python docs: read the front page: 2500 characters, 68 elements
- chrome, public page, MDN: read a reference page: 8093 characters, 126 elements
- firefox, other events, a menu that only shows on hover: not in the outline
- firefox, rich text box, a bare contenteditable div: not in the outline
- firefox, takes the keyboard? (UI Automation patterns first), reading the page: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), pressing a button: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), typing in a field: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), ticking a box: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), picking from a dropdown: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), bringing an element into view: the window couldn't be put behind first
- firefox, takes the keyboard? (UI Automation patterns first), following a link: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), reading the page: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), pressing a button: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), typing in a field: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), ticking a box: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), picking from a dropdown: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), bringing an element into view: the window couldn't be put behind first
- firefox, takes the keyboard? (older default action first), following a link: the window couldn't be put behind first
- firefox, window showing, not in front, read a page loaded in that state: the page says it is visible
- firefox, window covered by another window, read a page loaded in that state: the page says it is hidden; failed: The page isn't answering. Without the browser extension the browser wi
- firefox, window minimised, read a page loaded in that state: the page says it is hidden; failed: The page isn't answering. Without the browser extension the browser wi
- firefox, window read while showing, then covered, (what the page says of itself): the page says it is hidden
- firefox, window read while showing, then covered, press a button: failed: the page isn't answering (no web page was found in the window; page says: list, hidden=True; window minimised=False, in front=True)
- firefox, window read while showing, then covered, type in a field: failed: the page isn't answering (no web page was found in the window; page says: list, hidden=True; window minimised=False, in front=True)
- firefox, window read while showing, then minimised, (what the page says of itself): the page says it is hidden
- firefox, window read while showing, then minimised, press a button: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- firefox, window read while showing, then minimised, type in a field: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- firefox, public page, Wikipedia: read the main page: 10626 characters, 200 elements, more
- firefox, public page, Wikipedia: search for Ada Lovelace (its Search button): https://en.wikipedia.org/wiki/Ada_Lovelace
- firefox, public page, Python docs: read the front page: 2504 characters, 68 elements
- firefox, public page, MDN: read a reference page: 8059 characters, 126 elements
- edge, other events, a menu that only shows on hover: not in the outline
- edge, rich text box, contenteditable with role=textbox (a mail body): failed: The words were put in that box, but it is a rich text box and the page was not told, so it will most likely ignore them. That's a rich text box (the kind a mail body or a chat message is written in). Without Pyxi's browser extension its text can't be set in a way the page notices. Don't try again: say what is left for them, and that the extension can do it (Settings, Connect your browser).
- edge, rich text box, a bare contenteditable div: failed: That's a rich text box (the kind a mail body or a chat message is written in). Without Pyxi's browser extension its text can't be set in a way the page notices. Don't try again: say what is left for them, and that the extension can do it (Settings, Connect your browser).
- edge, takes the keyboard? (UI Automation patterns first), reading the page: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), pressing a button: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), typing in a field: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), ticking a box: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), picking from a dropdown: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), bringing an element into view: the window couldn't be put behind first
- edge, takes the keyboard? (UI Automation patterns first), following a link: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), reading the page: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), pressing a button: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), typing in a field: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), ticking a box: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), picking from a dropdown: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), bringing an element into view: the window couldn't be put behind first
- edge, takes the keyboard? (older default action first), following a link: the window couldn't be put behind first
- edge, window showing, not in front, read a page loaded in that state: the page says it is visible
- edge, window covered by another window, read a page loaded in that state: the page says it is visible
- edge, window minimised, read a page loaded in that state: the page says it is hidden; failed: The page isn't answering. Without the browser extension the browser wi
- edge, window read while showing, then covered, (what the page says of itself): the page says it is visible
- edge, window read while showing, then minimised, (what the page says of itself): the page says it is hidden
- edge, window read while showing, then minimised, press a button: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- edge, window read while showing, then minimised, type in a field: failed: the page isn't answering (the window is minimised; page says: list, hidden=True; window minimised=True, in front=True)
- edge, public page, Wikipedia: read the main page: 8688 characters, 200 elements, more
- edge, public page, Wikipedia: search for Ada Lovelace (its Search button): https://en.wikipedia.org/wiki/Ada_Lovelace
- edge, public page, Python docs: read the front page: 2500 characters, 68 elements
- edge, public page, MDN: read a reference page: 7991 characters, 123 elements

## How long a look takes

- chrome: reading a local page took 219 to 640 ms (median 297 ms).
- firefox: reading a local page took 125 to 406 ms (median 203 ms).
- edge: reading a local page took 203 to 500 ms (median 312 ms).
