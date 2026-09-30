# Draft Reddit Data API access request

**Not submitted.** Fill in the account-specific fields accurately before submitting
through [Reddit's support form](https://support.reddithelp.com/hc/en-us/requests/new).

**Application:** BigPlays

**Reddit username / contact email:** [Your account and contact details]

**Personal/non-commercial or commercial use:** [Choose the accurate classification]

**Application/repository URL:** [Your project URL, if applicable]

**Purpose and requested access:**

BigPlays records authorized sports streams and identifies timestamped plays from
live sports data. I am requesting read-only API access to public sports game
threads in r/CFB, r/nfl and r/nba to use fan reactions as a secondary highlight
ranking signal. The application does not post, vote, message users or moderate.

It matches threads to the two teams and scheduled date, then polls recent public
comments while the relevant games are being monitored. The initial local
configuration monitors two games, requests comments about every 15 seconds per
thread, and caches thread searches for five minutes. It uses OAuth, an identifying
User-Agent and rate-limit backoff, and will follow any limits imposed on approval.

**LLM use and data handling:**

A bounded sample (up to 20 comments, up to 800 characters per comment) would be
processed on my own machine by Qwen3 4B through Ollama alongside the relevant
play description and nearby plays. No comment text is sent to a cloud LLM API.
The model assesses whether the reaction concerns an impressive
play, routine action, officiating, injury or unrelated discussion. Usernames are
not sent to the model. No Reddit data is used to train or fine-tune a model.

Raw comment text is held only in memory for up to 15 minutes and is not stored in
the application's clip metadata. That metadata contains a brief model-generated
assessment, sampled activity metrics and links to supporting Reddit comments.
The collector removes deleted content when observed and invalidates affected
assessments. Please confirm whether this proposed inference and derived-metadata
use is permitted under the access granted, and any additional retention/deletion
requirements that must be implemented.

The purpose is to rank sports moments, not profile or target Reddit users. Reddit
availability does not gate recording or clipping, and the application will not
attempt to bypass approval, authentication or rate limits.
