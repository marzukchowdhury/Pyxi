// The words of the four demo clips, in one place, for the founder to read and change.
//
// Each clip has the same four beats: the problem (big type), the ask (one plain sentence),
// what Pyxi does (drawn in <id>.html), and the payoff. `says` is what Pyxi speaks, in its own
// voice; every line is short, the way the app keeps it, and each is shown as a caption while
// it is spoken. Wording that the app itself puts on screen ("Pyxi needs your OK", "Allow",
// "Don't", "You did it yourself", "Ledger finished", the pill "1/3 Click ...") lives in the
// scene pages and is copied from the app: change it there only if the app changes.
//
// Everything is made up: the site (Lumen), the people, the shop, the files.
window.STORY = {
  setting: {
    problem: ["Where is", "that <em>setting?</em>"],
    ask: "How do I make my profile private?",
    says: ["Click your photo, top right.", "Now Settings.", "Switch on Private profile.", "That's it. You're private now."],
    payoff: "No tutorial. No twelve minute video.",
  },
  post: {
    problem: ["Long post.", "<em>Zero</em> patience."],
    ask: "What's this post about?",
    says: ["It's a free comet night, this Friday from eight.", "Park in the lower lot.", "And go after ten for the best view."],
    payoff: "The gist, pinned beside the page.",
  },
  pivot: {
    problem: ["Sales by region.", "By <em>five.</em>"],
    ask: "Make me a pivot table of sales by region.",
    says: ["Ledger's drawing the pivot now.", "Ledger's done."],
    payoff: "A teammate does it while you carry on.",
  },
  desktop: {
    problem: ["214 files.", "All called <em>Screenshot.</em>"],
    ask: "Organise my desktop.",
    says: ["The plan's on the card.", "Done. 214 files are in 6 folders now."],
    payoff: "It asks before it moves a thing.",
  },
  // the end of every clip, and of the film
  name: "Pyxi",
  tag: "Just ask.",
  fine: "Screens simulated. Everything shown is something Pyxi does today.",
  heroEnd: "On Windows. heypyxi.com",
  // the order of the scenes in the film
  film: ["setting", "post", "pivot", "desktop"],
};
