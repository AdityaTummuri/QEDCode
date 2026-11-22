# Two-minute video

| Time | Picture | Words |
| --- | --- | --- |
| 0:00-0:15 | an agent's chat saying "fixed!", then the diff: a test got `@pytest.mark.skip` | "AI agents say fixed when it isn't. Sometimes they make the tests pass by turning them off." |
| 0:15-0:35 | `ladder.tape`: the check ladder, try 1 rejected at scope, try 2 grade A | "QEDCode never takes a model's word for it. Every change walks the same checks, cheapest first." |
| 0:35-1:05 | a real local fix on the RTX laptop (recorded during the Benchy main run), sped up and labelled | "On a 6 GB laptop, a small local model makes several tries. QEDCode keeps the one it can prove." |
| 1:05-1:20 | `qed receipt --link`, then the Receipt Viewer opening it | "Every result has a receipt anyone can re-run. Share it as a link; nothing is uploaded." |
| 1:20-1:40 | a pull request where the QEDCode Check action comments "Grade C · tests weakened" | "In a pull request, it catches the change that cheats its tests." |
| 1:40-2:00 | the four numbers from the results page | "Measured with hidden tests, losses included. uvx qedcode try." |

Terminal parts are VHS tapes (`*.tape`), so they re-render when the output changes:
`vhs drafts/video/try.tape`. The rest is recorded with OBS. Host on YouTube, embed on the site.
