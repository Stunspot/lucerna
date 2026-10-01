# Lucerna

An instrument for understanding.

Version 0.3.0

Give your AI a place to carry learning forward. Lucerna combines adaptive teaching craft with a local workspace for remembered context, curricula, learning paths, papers and interactive explanations.

Ask a quick question and get an explanation at the right depth. Start a larger subject and your agent can organize its concepts, remember useful connections, revise the route and resume the conversation later. The dialogue tells the agent what is helping; you do not have to maintain grades or learning records.

## Open a working lab

Start with Inside attention. Point a query vector, inspect its scores and watch the weighted mixture change. Make a future token overwhelmingly attractive, then see a causal mask keep its contribution at zero. Switch the mask off and inspect the leak. The included memory lab explores why retaining facts can still lose their meaning; chemistry and oscillator labs let you experiment beyond AI. All four work locally without importing anything.

## Explore what you are trying to understand

Paper Explorer imports arXiv papers and local PDF, HTML, Markdown or text sources. Your agent reads the material and authors explanations beside the relevant passages. Scenes can animate a mechanism, plot a relationship, compare quantities or let you change a meaningful parameter and see the consequence. Experiment centers the instrument; Read keeps the relevant source and your notebook together; Connections lets you inspect authored relationships and supporting passages. Full-text search finds matching passages across the library. Collections, named explanation editions and saved scene controls let you return to a useful line of inquiry. Source connections and model assumptions stay available for inspection.

The same workspace holds original lessons and visual aids. Built-in scenes provide a reusable rendering engine; when a subject needs a more specialized interactive artifact or illustration, your agent can use its available tools and deliver that through its normal preview or file interface.

Play, pause, scrub, move between steps and change controls. Narration uses a locally installed device voice when available, with a readable transcript alongside it. Export an exploration as a standalone HTML file when you want to revisit or share the source and its scenes.

## Choose your workspace's appearance

Use Visual skin in the header. LAMPLIGHT keeps the warm, dark instrument. APERTURE separates navigation into an optical dock beside suspended source panes on a refractive field. MARGIN opens a bound research folio, with an annotation margin and a ruled source register around the dark scientific stage. These are different interface bodies, not only different colors. Libraries, source reading, questions and controls follow the selected skin; the diagram's meaningful data colors remain intact.

The choice stays in this browser. Changing it keeps the open source, scene and experiment settings; it does not reload the explanation. Standalone HTML exports carry all three appearances too. Different browsers can keep different choices. When browser storage is unavailable, a choice still works for the open page but may not survive reopening it.

## Use it through normal conversation

Try: 'Explain why the sky is blue, then show me what changes at sunset.' Or: 'Teach me organic chemistry over time. Keep track of the connections that help and build a route we can adapt.' Or: 'Explore this paper with me. Show how the mechanism works, then help me judge what the results establish.'

Your agent runs the bundled tools. The browser displays the source and explanation, preserves your place, and can save a question with the current scene context. Return to your agent to have it answer or revise the explanation. Importing a paper makes its source available; the active agent then does the explanatory work.

## Start and keep your work

Follow [INSTALL.md](INSTALL.md) to install the skill and open your first workspace. You need a host agent that can read local skills and run Python, Python 3.10 or newer, and a modern browser. The package supplies its runtime dependencies. It needs no additional model API key; teaching uses your existing agent and its usual usage arrangement.

Learning state and the paper library are stored in a local folder you select, separate from the installed skill. Your host agent still processes the material through its normal model/tool environment. You can inspect or correct remembered context, export learning state, and request deletion of a memory or learner. Shared source documents and copies you exported elsewhere have separate custody.

PDF extraction can lose equation and figure layout; scanned pages may need a better source or OCR through your host's tools. The agent should inspect source features that matter to its explanation. The product provides teaching infrastructure and craft; it does not claim a measured learning advantage for every learner or subject.

The unchanged supplied teaching sources and their operative derivatives are identified in [Source lineage](references/source-lineage.md).
