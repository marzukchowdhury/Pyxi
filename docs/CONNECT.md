# Connecting your accounts

Blip can work with Gmail, Google Calendar, Google Docs, Discord and LinkedIn. Each one is
optional. Until you connect a service, Blip doesn't offer its tools to the model, so it costs
nothing.

Blip never sees your passwords. Google and LinkedIn sign you in on their own websites. Tokens
stay on this PC in `.blip/connectors/`, which git ignores. Reading happens straight away.
**Anything that reaches other people waits for an approval card that shows exactly what will
go out:** sending an email, posting, messaging, or inviting someone to an event.

Check where you stand at any time:

```bash
blip connect status
```

## Google: Gmail, Calendar and Docs (about 5 minutes, free)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project.
2. **APIs & Services > Library**: enable the **Gmail API**, the **Google Calendar API** and the
   **Google Docs API**.
3. **OAuth consent screen**: choose *External* and fill in the app name (Blip) and your email.
   Under **Test users**, add your own Gmail address.
4. **Credentials > Create credentials > OAuth client ID > Desktop app**, then **Download JSON**.
5. Save that file as `blip/.blip/google_client.json`.
6. Run `blip connect google`, or press **Connect** in *Settings > Connected accounts*. Sign in,
   then allow access.

Google will warn that the app isn't verified. That's expected for your own test project:
choose *Continue*. While the project is in *Testing*, Google expires the sign-in after 7 days.
Reconnect when that happens, or publish the app to stop it.

Things to try:

- "My friend Sam emailed my recommendation letter. Fix the grammar." Blip finds the email,
  saves the attachment to `Documents/Blip`, and Quill writes a corrected copy beside it
  ("LOR (fixed by Blip).docx"). Then Blip offers to send it back. You approve the reply before
  it goes.
- "What's on my calendar this week?" or "Add lunch with Nabiha on Friday at 1."
- "Read this Google Doc and explain it to me."

## Discord (about 3 minutes)

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications),
   choose **New Application**, open **Bot**, then **Reset Token**, and copy the token.
2. On the same page, turn on **Message Content Intent**.
3. Open **OAuth2 > URL Generator**, tick the `bot` scope, then the *Read Messages/View Channels*
   and *Send Messages* permissions. Open the generated URL and add the bot to your server.
4. Add `DISCORD_BOT_TOKEN=your-token` to `blip/.env` and restart Blip.

Try: "What did people say in #general today?" or "Tell #team I'll be 10 minutes late." The
second one is approved first.

## LinkedIn (posting to your profile)

LinkedIn's API lets apps post for you, but not read your feed or messages. For reading, Blip
sends Scout to do it in a browser window you can watch.

1. Go to [linkedin.com/developers](https://www.linkedin.com/developers/apps) and create an app.
   It needs a LinkedIn company page; a simple page you create for Blip works.
2. Under **Products**, add **Sign In with LinkedIn using OpenID Connect** and **Share on
   LinkedIn**.
3. Under **Auth**, add this redirect URL: `http://localhost:8767/linkedin/callback`
4. Add `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` to `blip/.env`.
5. Run `blip connect linkedin`. Access lasts 60 days.

Try: "Draft a LinkedIn post about shipping Blip and post it." Quill writes it, and you see the
exact text before anything is posted.

## Premium voices (optional)

The on-device voice is free and private. For a more human sound, add one of these keys to
`blip/.env` and pick a voice in the **Voice** tab:

- `ELEVENLABS_API_KEY` from [elevenlabs.io](https://elevenlabs.io/app/settings/api-keys).
  It's the most natural-sounding option and includes Bengali.
- `GEMINI_API_KEY` from [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
  Expressive voices that act out each line's tone.

If a premium voice fails (no credit left, or offline), Blip falls back to the on-device voice
for that line and tells you why.
