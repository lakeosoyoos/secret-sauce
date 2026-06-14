Secret Sauce — test fixtures (small REAL subsets)
=================================================

These are tiny, real OTDR captures copied verbatim from on-disk production
data. They exist so the e2e test suite (desktop/tests/) can exercise the full
"Run analysis" flow against the genuine engines without needing the multi-
gigabyte source folders. Total size is ~1.2 MB (well under the 10 MB cap).

Provenance
----------
sor/   8 .sor files — TUCROM449..TUCROM456_1550.sor
       Source: "Beta Duplicates" job, ROMERO -> TUCUMCARI, ~95 km long-haul,
       production regime. The pair TUCROM453 ~ TUCROM454 is the ONE real
       confirmed duplicate in that job (~100% likelihood); the surrounding
       fibers are non-dup neighbors. Including the dup pair lets the e2e test
       assert that "Confirmed duplicates" actually has a row.

json/  8 .json files — ELMMIL0001..ELMMIL0008_1550 .json
       Source: ELMMIL 1152-file production folder (single-wavelength 1550 nm
       traces). NOTE the deliberate trailing space before ".json" in every
       filename — it is part of the real EXFO export naming and is preserved
       on purpose (it is one of the filename edge cases the engine handles).

Do not edit these files. They are byte-for-byte real captures; any change
would invalidate the verdicts the tests assert against.
