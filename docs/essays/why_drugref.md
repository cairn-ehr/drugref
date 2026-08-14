# Why Drugref?

At the moment a medicine is prescribed, a list of drug facts is not enough. A clinician
needs an answer to a harder question: **what do those facts mean for this patient, here
and now?**

About twenty-six years ago, clinicians in Australia, Germany and Canada set out to build
an answer. They called the project drugref.org: a comprehensive pharmacological reference
for clinical use, freely available without paywalls or other access barriers. The idea was
sound, but the work was too large to sustain with volunteer contributors alone. The
project eventually ended, although parts of it lived on in projects including the
Canadian OSCAR electronic health record.

Nearly three decades later, the need remains. The paradox is that we are no longer short
of high-quality open data. UNII and GSRS describe substance identity and composition;
ChEBI provides chemical information; MED-RT and MeSH provide classifications and clinical
relationships; RxNorm supplies normalised drug names and identifiers. These resources are
valuable, carefully maintained and freely accessible. But they are ingredients, not yet a
clinically focused reference that can be used at the point of care.

The difficult part is not finding another warning. It is judging the warning's clinical
weight. Does an interaction make a combination absolutely contraindicated, or does it
merely justify monitoring? Is the evidence based on patient outcomes, pharmacokinetic
measurements, a case report, animal data, or theoretical mechanism? Does a renal-dose
recommendation apply to this degree of impairment? Is a pregnancy warning supported by
evidence of harm, or mainly by an absence of evidence? The same questions arise for
hepatic disease, breastfeeding, infancy, pharmacogenomics, diet, supplements and every
other part of the patient's context.

Existing sources often disagree, use different terminology, or repeat claims whose
evidential basis is difficult to trace. Presented without context, this material can
produce long lists of technically correct but clinically unhelpful alerts. Clinicians
learn to click through them, and the important warning becomes harder to distinguish from
the noise. A useful reference must therefore preserve more than the claim itself. It must
also preserve its provenance, the evidence supporting it, its clinical significance and
the reasoning behind the judgement.

That is the purpose of Drugref V2. It is being built as an open, vendor-independent drug
information commons whose knowledge can be inspected by people and processed by software.
The current reference build already contains a reproducible registry of 19,438 active drug
moieties, cross-references to major open vocabularies, classifications from MED-RT and
MeSH, salt and solvate composition from GSRS, and candidate indications,
contraindications and interactions. Curated judgements sit in a signed, append-only
overlay: corrections supersede earlier assertions instead of silently erasing them, and
known gaps are published as questions rather than hidden as missing data. The public API
and much of the clinical content are still to come, but the project is no longer merely a
proposal; its technical and evidential foundations are being tested against real data.

AI makes this second attempt feasible, but not because an AI-generated statement should
be mistaken for clinical truth. Properly directed agents can search large bodies of
literature, compare sources, extract candidate claims, follow citation trails and expose
contradictions at a scale that would otherwise consume years of specialist labour. Human
expertise remains the scarce and decisive resource. The opportunity is to concentrate it
on verification, clinical interpretation and curation rather than on the mechanical work
of finding and transcribing information.

The aim is to give a clinician reviewing a prescription or drug chart the information
needed to answer practical questions: Is this medicine indicated here? What dose is
appropriate for this particular patient? Is it compatible with the patient's other
medicines, supplements and diet? How do renal or hepatic impairment, genotype, pregnancy,
breastfeeding or age change the balance of benefit and harm?

Those answers must also be expressed in a form that algorithms and AI agents can evaluate
automatically. Decision-support software should be able to identify a genuinely dangerous
constellation and explain why it matters, while avoiding the indiscriminate flood of
low-value warnings that drives alert fatigue. The global core must remain freely available
to every health system; country-specific products, rules and reimbursement data can attach
as local layers without fragmenting or enclosing the commons.

Drugref will not replace clinical judgement, nor should it try to. Its ambition is to make
the evidence behind safer prescribing **traceable, computable and available to everyone**.
