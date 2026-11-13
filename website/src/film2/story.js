/**
 * The Pyxi launch film: every line in it, in one place. (Brief: docs/FILM-MOCKUP.md.)
 *
 * THIS IS A DRAFT FOR THE FOUNDER TO CHANGE. Nothing is drawn from it yet.
 * Change any line here and the film changes with it: the big type, the captions and Pyxi's
 * voice are all made from this file.
 *
 * Each scene has the same four beats:
 *   problem  the big type, two short lines at most. The word in [brackets] is the one in amber.
 *   ask      what the person says to Pyxi, word for word. Shown in quotes.
 *   reply    what Pyxi says out loud, in its own voice. Two short sentences at most, as in the app.
 *   payoff   what is left on screen to read, and the last line of type if there is one.
 *
 * `does` says where in the app this really happens, so the film never shows what Pyxi can't do.
 * `clip: true` means the scene also stands alone as a 15 second clip with the name on the end.
 * All names, places, files and numbers are made up. No em dashes, no emoji.
 */
export const STORY = [
  {
    id: "open",
    problem: ["Your computer can do", "almost [anything]."],
    problem2: ["If you know [how]."],
    ask: null,
    reply: null,
    notch: ["Waking up", "Hold Ctrl Alt to talk", "Listening"],
    shows: "Black. Pyxi's notch drops from the top edge, the firefly spinning in it: 'Waking up'. It changes to 'Hold Ctrl Alt to talk' and the two keys appear, large. They press down on the beat and the notch opens into 'Listening' with its waveform. Then the type lands.",
    payoff: { type: ["Or just [ask]."] },
  },
  {
    id: "post",
    clip: true,
    problem: ["Long post.", "Zero [patience]."],
    ask: "What's this post about?",
    reply: "It's a recall notice: the Halden kettle's handle can crack. If yours was made before March, you get a free replacement.",
    shows: "A long post on a made-up social site (Lumen). A card pins beside it: the gist and four points, each tied by a line to its words in the post.",
    payoff: { hold: "The card, readable." },
    does: "explain_on_screen (the notes card): src/pyxi/companion/notes.py",
  },
  {
    id: "heart",
    clip: true,
    problem: ["Biology test.", "[Tomorrow]."],
    ask: "Explain how the heart pumps, on this diagram.",
    reply: "Follow the blood. In from the body, out to the lungs, back, then out to everything.",
    shows: "A heart diagram in a textbook page. Red name tags land on the parts one at a time, and a red line traces the blood from one to the next.",
    payoff: { hold: "All seven parts named, the whole route drawn.", type: ["It explains [on] the picture."] },
    does: "explain_on_screen, on_picture style (tags pinned to parts, routes traced): src/pyxi/companion/notes.py, aim.py",
  },
  {
    id: "engine",
    clip: true,
    problem: ["New RAM.", "No idea where it [goes]."],
    ask: "I want to add RAM to my PC. Can you show me in 3D how to put it in?",
    reply: "Open the two clips, line up the notch, and press down until both clips click.",
    shows: "A detailed 3D model of the inside of a PC builds itself over the page: the motherboard, the CPU and its fan, the memory slots. The camera flies in to the slots. The clips open, the stick lines up with its notch, and it presses in.",
    payoff: { hold: "The RAM seated, both clips closed, each step labelled: 1 open the clips, 2 line up the notch, 3 press until it clicks." },
    accuracy: [
      "The memory slots sit beside the CPU socket, upright, in a row of four.",
      "The stick goes in one way only: its notch is off centre and must meet the ridge in the slot.",
      "The clips at the ends of the slot open first, and close by themselves when the stick is pressed fully in.",
      "With two sticks, they go in the second and fourth slots from the CPU (the usual pairing; the film says to check the board's manual).",
      "The PC is off and unplugged in the picture.",
    ],
    does: "build_3d_model with the parts kit (ram_stick, chip, pc_fan), for 'how do I attach it' questions: src/pyxi/scenes/objects.py, src/pyxi/companion/prompts.py. The storyboard stills are a stand-in: real Pyxi output of a chip model, not yet the PC.",
  },
  {
    id: "shrink",
    clip: true,
    problem: ["The form says 2 MB max.", "Your PDF is [14]."],
    ask: "Make this small enough to upload.",
    reply: "Done. It's 1.8 megabytes now, and every page is still sharp.",
    shows: "A file, 'Lease agreement.pdf'. Its size counts down from 14.2 MB to 1.8 MB. It drops onto the upload box.",
    payoff: { hold: "The upload turns green: 'Lease agreement.pdf, 1.8 MB, uploaded'." },
    does: "Pixel, compress_pdf: src/pyxi/agents/pixel.py",
  },
  {
    id: "word",
    clip: true,
    problem: ["They sent a PDF.", "You need to change [one line]."],
    ask: "Turn this into Word.",
    reply: "Here it is as a Word file. I kept the original next to it.",
    shows: "The PDF page turns over and lands as an editable document. A cursor changes '14 March' to '21 March'.",
    payoff: { hold: "Two files side by side: 'Offer letter.pdf' and 'Offer letter.docx'." },
    does: "Pixel, pdf_to_word: src/pyxi/agents/pixel_pdf.py",
  },
  {
    id: "setting",
    clip: true,
    problem: ["Where is", "that [setting]?"],
    ask: "How do I make my profile private?",
    reply: "Three clicks. Start with your picture, top right.",
    shows: "On Lumen, a glowing mark lands on the thing to click: step 1 of 3, then 2, then 3. The pointer follows each one.",
    payoff: { hold: "The switch flips to Private.", type: ["No tutorial.", "No twelve minute [video]."] },
    does: "guide (marks the next thing to click, waits for your click): src/pyxi/companion/notes.py",
  },
  {
    id: "editor",
    clip: true,
    problem: ["New app.", "Forty [buttons]."],
    ask: "How do I add captions to this?",
    reply: "This one, under Text. Click it and I'll show you the next step.",
    shows: "A made-up video editor (Reelbox), crowded with buttons. Everything dims except the one that matters, with Pyxi's mark on it.",
    payoff: { hold: "Captions appear on the clip." },
    does: "guide, and the app tour (explain_on_screen on the app's window): src/pyxi/companion/prompts.py",
  },
  {
    id: "pivot",
    clip: true,
    problem: ["Sales by region.", "By [five]."],
    ask: "Make me a pivot table of sales by region.",
    reply: "There you go. Pacific is your best region, at four thousand five hundred and eighty dollars.",
    shows: "A sales sheet, twelve rows. The pivot builds beside it, one region at a time, each total counting up.",
    payoff: {
      hold: "Atlantic $2,365. Ontario $2,915. Pacific $4,580. Prairies $2,495. Quebec $1,820. Total $14,175.",
      note: "These are the totals Pyxi really produced on 12 November, and they add up.",
    },
    does: "Ledger, pivot tables in Google Sheets and Excel: src/pyxi/agents/ledger.py",
  },
  {
    id: "desktop",
    clip: true,
    problem: ["214 files.", "All called [Screenshot]."],
    ask: "Organise my desktop.",
    reply: "Here's my plan: six folders, nothing deleted. Say Allow and I'll do it.",
    shows: "A plan card: Screenshots 96, Documents 41, Pictures 38, Installers 17, Videos 12, Everything else 10. The person says 'Allow'. The icons fold into six folders.",
    payoff: { hold: "A clean desktop with six folders.", note: "96 + 41 + 38 + 17 + 12 + 10 = 214." },
    does: "the desktop tidy, plan first and then Allow: src/pyxi/tidy.py",
  },
  {
    id: "letter",
    clip: true,
    problem: ["Sam's email.", "Somewhere in your [inbox]."],
    ask: "Find the email Sam sent me, fix the letter in it and send it back.",
    reply: "Found it, from Tuesday. I fixed the date, a name and a missing word. Shall I send it?",
    shows: "Three quick beats. The mail found, by sender and day. The letter with its three fixes marked. A card: 'Send this to Sam? Allow / Not now', with the fixed file attached.",
    payoff: { hold: "The card, then 'Sent to Sam'.", type: ["It asks before it [sends],", "pays or deletes."] },
    does: "mail search, fixing a Word attachment, and the Allow card before sending: src/pyxi/connectors/google.py, src/pyxi/companion/tools.py",
  },
  {
    id: "quick",
    problem: null,
    asks: ["Set an alarm for seven.", "Turn these photos into one PDF.", "Quiz me on these slides.", "Coffee near me?"],
    reply: null,
    shows: "Fast cuts on the beat, one second each, the result only: the alarm in the list, one PDF from three photos, a flashcard, a map with two places marked.",
    payoff: { type: ["And a lot of", "[other things]."] },
    note: "The brief's line was 'And a few hundred other things'. Nothing backs that number, so this draft says 'a lot of'. Founder's call.",
    does: "alarms (routines), Pixel images_to_pdf, Sage quizzes, places on the map: src/pyxi/companion/tools.py, src/pyxi/agents/",
  },
  {
    id: "end",
    problem: null,
    ask: null,
    reply: "That voice you've been listening to? That's me. I'm Pyxi.",
    shows: "One beat of silence. The camera flies into the firefly. Its light becomes the mark beside the name.",
    payoff: {
      type: ["Pyxi.", "Just [ask]."],
      small: ["Free beta for Windows 10 and 11", "pyxi-site.vercel.app", "Screens simulated. Everything shown is something Pyxi does today."],
    },
  },
];
