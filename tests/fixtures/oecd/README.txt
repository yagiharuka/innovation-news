OECD regression fixtures
Observed on 2026-10-11 UTC from configured public Jina Reader transports.
- challenge.txt: actual HTTP 200 challenge response for the policy article.
- data-sitemap-xml.txt: first two complete entries from the observed 111,007-byte
  XML-format en-data-sitemap.xml response. All remaining entries are omitted;
  trailing whitespace is normalized.
- policy-metadata.txt: synthetic Jina wrapper using the verified official title
  and publication day (29 September 2026), with a paraphrased short excerpt.
  Its midnight timestamp is a fixture value, not an observed publication time.
- space-metadata.txt: synthetic shortened Jina response using its observed title,
  URL and Published Time (2026-10-06T14:53:00+02:00), with a paraphrased excerpt.
These fixtures establish parser behavior, not successful end-to-end recovery of
live OECD collection or the exact cause of the earlier GitHub Actions failure.
No access challenge was solved, and no alternate access restrictions bypassed.

Sources:
https://r.jina.ai/http://www.oecd.org/en-data-sitemap.xml
https://www.oecd.org/en/blogs/2026/09/how-economic-security-is-shaping-science-technology-and-industrial-policy.html
https://www.oecd.org/en/blogs/2026/10/what-is-the-space-economy-and-why-does-it-matter.html
