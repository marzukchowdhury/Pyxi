# Bengali voice samples and test results

Every speaking sample reads the same five sentences: a greeting, reassurance, a reminder with a date and an amount in taka, an email summary, and a sentence mixing in English loanwords.

> হাই, আমি পিক্সি। তোমার স্ক্রিনেই থাকি, ভাড়া দিই না, আর স্প্রেডশিটে আমি অদ্ভুত রকমের ভালো।
> চিন্তা কোরো না, আমরা এক ক্লিক করে করে এগোব।
> আজ বিকেল চারটায় তোমার ডাক্তারের অ্যাপয়েন্টমেন্ট আছে, আর বিদ্যুৎ বিলটা বৃহস্পতিবারের মধ্যে দিতে হবে, মোট এক হাজার দুইশো পঁচাত্তর টাকা।
> ইমেইলটা পড়ে দেখলাম। ওরা জানতে চাইছে তুমি সোমবারের মিটিংয়ে আসতে পারবে কি না।
> ফাইলটা ডাউনলোডস ফোল্ডারে সেভ করে রেখেছি, চাইলে এখনই খুলে দিতে পারি।

## Speaking: the clips

| File | Engine and voice | Runs | Time to first audio | Cost per minute of speech | Round-trip errors* |
|---|---|---|---|---|---|
| `azure-bn-BD-nabanita.mp3` | Azure Neural, Nabanita (Bangladesh, female) | cloud | about 1 s here (0.2 to 0.4 s typical for Azure) | about $0.012 | 6.6% |
| `azure-bn-BD-pradeep.mp3` | Azure Neural, Pradeep (Bangladesh, male) | cloud | same | about $0.012 | 6.6% |
| `azure-bn-IN-tanishaa.mp3` | Azure Neural, Tanishaa (India, female) | cloud | same | about $0.012 | 7.5% |
| `openai-gpt-audio-mini-marin.wav` | OpenAI gpt-audio-mini via OpenRouter, Marin | cloud | 1.4 s | about $0.004 | 10.4%, **mispronounced a word** |
| `openai-gpt-audio-mini-cedar.wav` | OpenAI gpt-audio-mini via OpenRouter, Cedar | cloud | 1.2 s | about $0.004 | 10.1%, **mispronounced a word** |
| `local-coqui-bn-female.wav` | Coqui VITS Bengali (female), on this PC via sherpa-onnx | offline | about 1 s per sentence on the CPU | free | 9.0% |
| `pyxi-answer-end-to-end.wav` | The same voice as built into Pyxi ("Mitali"), a real answer to a spoken Bengali question | offline | under 1 s per sentence on the CPU | free | 3.2% |

\* Character error rate when Gemini 3.5 Flash transcribes the clip back and we compare it with the text it was given. Most of the remaining errors are not mistakes in the speech. They are the transcriber writing numbers as digits (১,২৭৫) and spelling কোরো as করো. Lower is clearer.

Notes:

- The Azure clips were made with Microsoft Edge's free read-aloud service, which uses the same Azure Neural voices. That route is unofficial and only suitable for making samples. In the app, Azure would be called with a proper Azure Speech key on the beta server.
- OpenAI's voices mispronounce বৃহস্পতিবার (Thursday). The model's own transcript says it read the word correctly, but a listener hears "পরশুদিন" (the day after tomorrow), "২৫ শে" (the 25th) or "ভবিষ্যতের" (of the future). Every voice tried (Marin, Cedar, Coral, and the full gpt-audio model) did it, even when reading one sentence at a time with strict instructions. For reminders and bills that is a real risk.
- The local Coqui voice is clear enough to understand but flat, and it barely pauses at commas. It needs a one-time 108 MB download and is the fallback that keeps Pyxi talking offline.
- Not sampled because there is no key on this PC: Gemini TTS (already an engine in Pyxi; it supports bn-BD) and ElevenLabs (Bengali only on v3 and v4 models, not Flash; it costs several times more than Azure).

## Hearing: accuracy on real Bengali speech

15 clips of real people, about 63 seconds in total, with known transcripts: 8 from Mozilla Common Voice (volunteers, varied microphones), 4 short phrases from OpenSLR 53 (phone recordings from Bangladesh), and 3 from MADASR (rural, dialect speech). Character error rate (CER) and word error rate (WER) after removing punctuation; lower is better.

| Recognizer | Runs | CER | WER | Speed | Cost |
|---|---|---|---|---|---|
| Whisper small (Pyxi's current non-English model) | this PC | 168% | 161% | | free |
| Whisper large-v3-turbo | this PC | 48% | 92% | 0.18x real time (RTX 3070) | free, 1.6 GB |
| Whisper large-v3 | this PC | 35% | 78% | 0.37x real time (RTX 3070) | free, 3 GB |
| Whisper small fine-tuned for Bengali (ammaraldirawi) | this PC | 23% | 53% | 0.16x real time | free, 250 MB |
| Whisper medium fine-tuned for Bengali (SayedShaun) | this PC | 19% | 27% | 0.12x real time | free, 1.5 GB |
| Whisper medium, Bengali.AI winner (tugstugi, regional) | this PC | 18% | 31% | 0.14x real time | free, 1.5 GB |
| **Gemini 3.1 Flash-Lite via OpenRouter** | cloud | **14%** | **21%** | 1.7 s per clip | about $0.001 per minute |
| Gemini 3.5 Flash via OpenRouter | cloud | 13% | 20% | 1.5 s per clip | about $0.007 per minute |

Over 100% means the model made up more text than was said (Whisper small and turbo fall into repeating loops on Bengali). Both Gemini models got the four OpenSLR phrases exactly right. Most of their remaining errors are on Common Voice clips where the reference itself is hard to hear, and on dialect spellings.

Language detection: on the same clips, Whisper small named the right language only about half the time (it guessed Greek, Polish and Japanese), but it was never unsure about English. English clips scored 0.92 or more for English and Bengali clips 0.14 or less. So in Automatic mode Pyxi can keep English on the PC and send only non-English speech to the Bengali recognizer.

## Decision and what was built

The founder chose **no new accounts**, which rules out Azure (and Gemini or ElevenLabs keys for testers).

**Hearing: Gemini 3.1 Flash-Lite via OpenRouter**, through the beta server for testers (it allows that model for transcription only). Offline fallback: the Bengali.AI Whisper medium model on the PC, `pyxi setup-voice --bengali` (1.5 GB). In Automatic, English stays on the PC.

**Speaking: Mitali**, the local Coqui Bengali voice (Apache 2.0), run directly on ONNX Runtime, so there's no new dependency. It downloads once (108 MB) when Bengali is first needed. Numbers are spelled out as Bengali words first, because the voice can't read digits. Anyone with their own Gemini or ElevenLabs key can still pick those voices, which speak Bengali; if they fail, Mitali takes over.

End to end, through a local copy of the beta server as a keyless tester: a spoken Bengali question was heard word for word in 1.7 s (cost $0.00015). Pyxi answered in natural Bengali, with the first audio 6.2 s after the question, most of it the model thinking. Transcribing the spoken answer back gave 3.2% character errors.

Not chosen: gpt-audio-mini (mispronounces words) and any stock Whisper model for Bengali.

If Azure ever becomes acceptable, it would be the clearest and most natural voice (the three Azure samples above). It would plug in as a beta-server speech route with the same daily allowance.
