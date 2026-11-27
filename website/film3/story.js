// The words of the demo clips and the film, in one place, for the founder to read and change.
//
// Each clip has the same five beats: the problem (big type), the ask (one plain sentence),
// what Pyxi does (drawn in <id>.html), the lesson (big type again: what Pyxi just did, in
// words anyone gets), and the name. `says` is what Pyxi speaks, in its own voice; every line
// is short, the way the app keeps it, and each is shown as a caption while it is spoken.
// `how` is the one small line under the lesson. Wording that the app itself puts on screen
// ("Pyxi needs your OK", "Allow", "Don't", "You did it yourself", "Ledger finished", the pill
// "1/3 Click ...") lives in the scene pages and is copied from the app: change it there only
// if the app changes.
//
// The film opens with Pyxi saying what it is (`intro`), and closes on the four lessons in a
// list (`recap`) before the name.
//
// Everything is made up: the site (Lumen), the people, the shop, the files.
window.STORY = {
  intro: {
    // each line: what Pyxi says, and the big words that land with it
    lines: [
      { say: "Hi. I'm Pyxi.", type: ["Hi. I'm <em>Pyxi.</em>"] },
      { say: "I live on your screen.", type: ["I live on", "your <em>screen.</em>"] },
      { say: "I can see what you see.", type: ["I <em>see</em>", "what you see."], show: "see" },
      { say: "And I can hear you.", type: ["And I", "<em>hear</em> you."], show: "hear" },
      { say: "So when you're stuck, hold two keys and ask.", type: ["Stuck?", "Hold, and <em>ask.</em>"], show: "keys" },
    ],
    keys: ["Ctrl", "Alt"],
  },
  setting: {
    problem: ["Where is", "that <em>setting?</em>"],
    ask: "How do I make my profile private?",
    says: ["Click your photo, top right.", "Now Settings.", "Switch on Private profile.", "That's it. You're private now."],
    lesson: ["It <em>shows</em> you", "where to click."],
    how: "Pyxi sees your screen, so it points at the exact button.",
    payoff: "No tutorial. No twelve minute video.",
  },
  post: {
    problem: ["Long post.", "<em>Zero</em> patience."],
    ask: "What's this post about?",
    says: ["It's a free comet night, this Friday from eight.", "Park in the lower lot.", "And go after ten for the best view."],
    lesson: ["It <em>reads</em> it,", "so you don't have to."],
    how: "Any page, any app. The short version, pinned beside it.",
    payoff: "The gist, pinned beside the page.",
  },
  pivot: {
    problem: ["Sales by region.", "By <em>five.</em>"],
    ask: "Make me a pivot table of sales by region.",
    says: ["Ledger, my spreadsheet helper, is on it.", "Done. Your pivot is beside the data."],
    lesson: ["It <em>does</em> the work", "while you carry on."],
    how: "Spreadsheets, documents, PDFs. A helper works in the background.",
    payoff: "A teammate does it while you carry on.",
  },
  desktop: {
    problem: ["214 files.", "All called <em>Screenshot.</em>"],
    ask: "Organise my desktop.",
    says: ["Here's my plan. Nothing moves until you say so.", "Done. 214 files are in 6 folders now."],
    lesson: ["It <em>asks</em> first.", "Then it's done."],
    how: "Nothing is moved, sent or deleted without your OK.",
    payoff: "It asks before it moves a thing.",
  },
  // three everyday errands: clips of their own, not in the film
  coffee: {
    problem: ["Need coffee.", "Which <em>one?</em>"],
    ask: "Find me good coffee near here.",
    says: ["Here's what's near you.", "Fern and Finch is closest, and rated four point eight.", "Marlow's is open late.", "And Tin Kettle has the best pastries."],
    lesson: ["It <em>finds</em> the good", "places near you."],
    how: "Rating, distance, open now. Marked right on the page.",
    payoff: "The best picks, marked on the map.",
  },
  movie: {
    problem: ["Movie tonight.", "Seats going <em>fast.</em>"],
    ask: "Get two tickets for Starfall, tonight around seven.",
    says: ["First, your OK.", "Scout is picking two seats.", "Seats held. Paying is up to you."],
    lesson: ["It <em>books</em> the seats.", "You just pay."],
    how: "Pyxi never types your card. It hands you the payment page.",
    payoff: "It holds the seats. You pay.",
  },
  doctor: {
    problem: ["That check-up.", "Put off since <em>March.</em>"],
    ask: "Book me a check-up at Maple Grove Clinic, Friday morning.",
    says: ["First, your OK.", "Scout is filling in the form.", "Booked. Friday at nine forty."],
    lesson: ["It <em>fills in</em> the forms", "and books it."],
    how: "With the details you saved once. It asks before the final click.",
    payoff: "Booked, with the details you saved once.",
  },
  // the film's last list: the four lessons, short
  recap: { head: "One helper. On your screen.", rows: ["Shows you where to click", "Reads the long stuff for you", "Does the boring work", "Asks before it acts"] },
  // who is talking, over the captions
  you: "You say",
  // the end of every clip, and of the film
  name: "Pyxi",
  tag: "Just ask.",
  fine: "Screens simulated. Everything shown is something Pyxi does today.",
  heroEnd: "On Windows. heypyxi.com",
  // the order of the scenes in the film
  film: ["setting", "post", "pivot", "desktop"],
};
