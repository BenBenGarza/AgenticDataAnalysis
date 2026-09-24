# Decision log

## What I assumed, and why

- **Reviewers run it themselves**, with their own keys or a `.env` I share privately. Docker is the
  primary path: one command, and credentials only ever come from `.env`, never the repo or image.
- **"UI without a library"** rules out frontend frameworks and Markdown/UI libraries: the chat is
  plain HTML/CSS/JS with my own escaping Markdown renderer. FastAPI (a backend framework) serves it;
  Chart.js draws charts, since visualization libraries are allowed.
- **"Vendor SDK's messages endpoint"** allows the Anthropic SDK and API features (prompt caching,
  context editing, fallbacks), but not its Tool Runner, which would run the loop for me.
- **SQL plus a written interpretation and charts is "comprehensive analysis"**; no statistics tool.
- **One user, one question at a time.** A `users` table makes more users a code change, not a
  migration; the one-answer rule is enforced by the server (409) because browser tabs share a session.
- **Nothing is lost:** history is append-only and deletes are soft.
- **Dataset defaults:** relative dates are read against the last day of data; channel questions use
  session-level attribution; obfuscated values are reported and flagged, not hidden.
- **Claude Opus 5.5, high effort.** On the same question, Haiku 4.5 was ~15x cheaper but skipped
  verification and misstated a percentage; Opus 5.5 was correct at ~$0.05-0.10 per answer.

## What I cut or deprioritized

- **Hosted deployment**: it would spend my API credit, so it first needs auth and a spending cap.
- **Authentication and multi-user support** (schema ready), and **a code-execution tool** for
  statistics or forecasting.
- **A graded evaluation set**: instead, a re-runnable script runs 10 example conversations through
  the real app ([example-conversations.md](example-conversations.md)), plus 107 automated tests
  that fake Claude and BigQuery.
- **JavaScript unit tests**: the UI and the renderer's injection safety were tested in a browser.
- **Export, regenerate and rename**, and per-conversation budgets.

## Where I got stuck, and what I did

- **Setup and credentials**: Thinking of the best way to add credentials to my tool. I paused for a
  second because I was trying to think of the best way to do this with the final implementation that
  I wanted, not what I currently had (just a python script, no docker). I also had some issues with
  homebrew, error handling when credentials were missing. Decided to move in parts, first get my
  python script working with the main stuff, including credentials in the best way like using gcloud
  to login via browser, and then when I was ready to add this to a docker file for easy installation
  adapt the solution to that.
- **Dataset traps.** I had some issues with the data at first for understanding what type of info I
  had. My first query showed 0% cart-to-purchase because the `item_category` differs between view
  and purchase events. I profiled the data and tried to understand better the information that I had
  to correctly map it. Documented some notes (`docs/dataset_notes.md`) and added example queries for
  validation.
- **Persistence**: Solving this for my current solution while preparing for the future is what got me
  stuck. This persistence also depends on the responses of the model so there are some scenarios
  there that could affect how we store the information (response block - changed prompts/tools).
  Decided to focus on solution for what I currently have, assume only one user (but prepare for more)
  and use this feature to add more value, like the ability to have all the conversation there for
  modification and history.
- **Stand Alone**: Thinking about the tradeoffs and trying not to do an overly complicated solution
  for what I actually need. Creating a script or adding docker to simplify the installation and create
  this stand alone. I decided to go with docker which might be overkill for what I currently have
  but it gives an idea on how to grow this easily (horizontally) for future enhancements.


## With another 40 hours

1. **Evaluation harness**: 40-50 questions with SQL-verified answers, graded automatically,
   tracking accuracy, cost and latency across prompt changes.
2. **Hosted demo**: Cloud Run with its own identity (no keys), a password and a spending cap.
3. **Semantic layer**: predefined session, order and funnel views, for simpler SQL, fewer
   mistakes and less data scanned.
4. **Deeper analysis**: a sandboxed Python tool for significance tests and forecasting, and
   links from each number in an answer to the query behind it.
5. **UI and quality**: export, regenerate, conversation search, Playwright end-to-end tests,
   and an accessibility pass.
6. **User management**: The ability to serve multiple users and manage logins
7. **Clear division of front and back**: For scalability (horizontal)
