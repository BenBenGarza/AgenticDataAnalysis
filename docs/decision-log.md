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
- **A systematic evaluation set**: I checked quality with ~20 real questions (follow-ups, charts,
  restarts, stop, failures) plus 107 automated tests that fake Claude and BigQuery.
- **JavaScript unit tests**: the UI and the renderer's injection safety were tested in a browser.
- **Export, regenerate and rename**, and per-conversation budgets.

## Where I got stuck, and what I did

- **Setup and credentials**: Thinking of the best way to add credentials to my tool. I paused for a 
- second because I was trying to think of the best way to do this with the final implementation that
- I wanted, not what I currently had (just a python script, no docker). I also had some issues with
- homebrew, error handling when credentials were missing. Decided to move in parts, first get my 
- python script working with the main stuff, including credentials in the best way like using gcloud 
- to login via browser, and then when I was ready to add this to a docker file for easy installment 
- adapt the solution to that.
- **Dataset traps.** I had some issues with the data at first for understanding what type of info I 
- had. My first query showed 0% cart-to-purchase because the `item_category` differs between view 
- and purchase events. I profiled the data and tried to understand better the information that I had
- to correctly map it. Documented some notes (`docs/dataset_notes.md`) and added example queries for 
- validation
- **Persistence**: This make me think of what I currently had and what needed to change for this to 
- work, at first it seems that it was going to be more complicated. After a careful consideration
- I realize that because of the nature of the context we just need to store the responses we get and
- that works for history and persistence. There was an issue with the model I'm using (Opus 5.5) that 
- it ties thinking (response blocks) to the exact prompt and tools. That means that if something changes
- we can't use those all blocks anymore because they won't match with the current ones. This was
- solved by adding an option to ignore the blocks that failed and just re-process this if needed.
- Ex. After adding a new tool or making a change in a prompt, if you ask a new question in an existing
- chat it will cause it to failed because the blocks for the previous responses (which we have stored
- in the db) don't match with the new ones (because of the changes).
- **Stand Alone**: My first iteration for this was a simple cli with the agent that worked but the
- displaying of information wasn't the best. I was stuck with this for a little way just thinking of
- the best approach. The issue that I had here is thinking what I need for what I currently had and
- trying to compare that to what I could need in the future. My app is simple enough to be run manually
- in a computer but thinking someone doing everything I did leaves room for mistakes. This could have
- been a python with django solution but implementation right now is so simple so decided it wasn't 
- worth the overhead right now. Another thing here was dividing the front from the backend (microservices)
- so that docker can have 2 containers, and we can manage more traffic by growing horizontally but
- again, maybe complicating this at this point. Credentials was another issue here. 
- At the end I tried to do a simple solution (docker) that is a little overkill for what I currently 
- have but it does simplify the installation and gives me the structure I need to think about the future.
- It's a monolith application but having backend api makes it easier to divide this into different
- services.


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
6. **User management**: The ability to service multiple users and manage logins
7. **Clear division of front and back**: For scalability (horizontal)
