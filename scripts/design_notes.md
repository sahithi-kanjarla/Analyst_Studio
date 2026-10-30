# Design notes: what we borrow

Research status: the cloud session could reach the Microsoft Power BI product page. julius.ai, hex.tech and tableau.com did not resolve (blocked by the session's network allow-list), and claude.ai returned HTTP 403 to the fetch. The lines for those four are therefore from the brief's principles and general knowledge of the products, and are marked as such. They should be checked against the live pages once the hosts are allowed.

1. **Julius AI** (not reached; general knowledge): a chat-first analyst that answers questions over uploaded files. We borrow the idea that a question and its answer belong together on one screen, but we keep the answer checked by code and never let the model write numbers.
2. **Hex** (not reached; general knowledge): notebooks where the query, the chart and the prose sit together. We borrow "the prose and the chart that proves it on one page", not the notebook editing.
3. **Power BI product page** (read): a hero with one headline and a free-trial call to action, then a carousel of short feature cards, each with one screenshot of a real dashboard. We borrow the one-message-per-section rhythm, and we drop the carousel and the pricing wall: our tour is one framed screen with tabs.
4. **Power BI page, customer proof** (read): a single productivity statistic beside short quotes. We borrow the restraint: one number of proof, no badge wall.
5. **Tableau Pulse** (not reached; general knowledge): metrics pushed as short cards with a plain-language change line. We borrow the "one sentence on what changed" line under each headline figure.
6. **Claude.ai product pages** (not reached, 403; general knowledge): large type, warm neutral surfaces, one accent colour used sparingly, and a framed product screen that changes when a feature is chosen. We borrow the framed screen with a row of tabs above it.
7. **Power BI page, navigation** (read): plain text links and a single primary action per section. We borrow "one primary button per screen" for the upload box and the sample button beside it.
8. **Hex and Julius** (general knowledge): show the question in the reader's own words before the result. We borrow it for the report's answer line.
9. **Tableau Pulse** (general knowledge): hide what is not needed by default and let the reader open detail. We borrow the collapsed "view more" section.
10. **Our own rule**: no purple or blue gradients, no emoji icons, no stock illustrations, no rows of identical cards. The tour shows the app's real screens, built from the bundled sample run, so nothing on the landing page is invented.
