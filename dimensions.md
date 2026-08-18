# Image-model evaluation dimensions

The working taxonomy. Every candidate item gets mapped onto one or more of these.
A dimension is only added here after Shayan approves it; the classifier may
*propose* new ones but must never assume one exists.

## Generation

**prompt adherence** — Did the requested content appear?
**text accuracy** — Is exact text present and correctly rendered?
**layout/spatial control** — Are objects where they should be? (e.g. Ideogram 4.0 reports layout control via bounding-box overlap metrics such as mIoU.)
**object fidelity** — Are the correct objects present?
**image quality** — Artifacts, anatomy, texture, lighting.

## Editing

**editing fidelity** — Did the requested changes occur?
**preservation** — Did unrelated areas stay stable?

## Consistency

**identity/reference fidelity** — Does a person/product/brand remain recognizable?
**style fidelity** — Does the output follow a reference aesthetic?
**diversity** — Does the model collapse to very similar outputs?

## Production

**safety** — Unsafe content, copyright/likeness issues.
**latency** — How long generation takes.
**cost** — How much computation/API/GPU time it consumes.

Quality, latency and cost are tracked together because a production system has
to optimize all three, not just quality.
