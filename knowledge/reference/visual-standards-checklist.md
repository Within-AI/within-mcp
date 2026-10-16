# Visual-standards checklist — workflow diagram renders

The required visual standard for a rendered Current State or Future State Workflow. Two
places use it:

- the **brief-drafter** cites it in every render / diagram packet, and instructs the
  executor to self-check the render against it before submitting;
- the **human visual gate** uses it after deterministic preflight and the agent
  structural gate pass. See [review order and implementation](deterministic-preflight.md).

Each item is a recurring defect, the rule that prevents it, and the remedy that has been
shown to work. Conformance is a floor, not the whole of visual judgment: the human still
owns legibility, density, and overall presentability. The band renderer enforces items 3
and 6 mechanically (label/node overlap and rail landing are geometry checks), and notes
(the small i) point at unfamiliar or incomplete content.

## Checklist

The stable event IDs V1–V6 correspond to items 1–6 below. A final accepted
`human.visual_review` records all six, on the latest LLM-cleared submission identity.

1. **Chips must not overflow the node.**
   - *Defect:* on multi-surface nodes the system / surface chips spilled past the node
     boundary.
   - *Rule:* every chip renders inside its node's bounds.
   - *Remedy:* size nodes to a uniform height that fits the chip block; pack chips into a
     bottom-anchored multi-row block; collapse the surplus into a trailing `+N` overflow
     chip and keep the full surface list in the tooltip.

2. **Chips must not collide with a neighbour's chips.**
   - *Defect:* a node's chip block ran into the adjacent node's chips.
   - *Rule:* chip blocks stay within node bounds at the chosen column / row pitch.
   - *Remedy:* set node size and pitch so the packed chip block never reaches a neighbour;
     widen pitch before shrinking chips illegibly.

3. **Edge and branch labels must never be hidden behind a node.**
   - *Defect:* then-branch and disposition labels were drawn under a node box and became
     invisible; connector draw-order overhung labels into boxes.
   - *Rule:* every edge label is fully visible and reads clear of all nodes.
   - *Remedy:* draw labels on a layer above the nodes; give each label a solid (white)
     background pill so no line or box breaks the text; lift labels into a clear band
     where a row is crowded.

4. **Every edge label must be attributable to exactly one edge.**
   - *Defect:* two near-parallel edges had both labels stacked at the same midpoint, so
     neither could be tied to its edge.
   - *Rule:* one label per edge, unambiguously attached to that edge.
   - *Remedy:* widen column pitch and give each fan-out edge its own corridor / lane;
     anchor each label beside its edge near the source end; offset near-parallel edges so
     they read as distinct lines.

5. **Connectors and drop-lines must not cross annotation text.**
   - *Defect:* drop-lines ran across the letters of a workspace annotation.
   - *Rule:* no connector passes through readable text.
   - *Remedy:* put a solid background pill behind every annotation so a crossing line is
     occluded by the pill, not the reverse; trim annotations to the essential phrase and
     move detail to chips or the tooltip.

6. **Every edge must land on a visible target.**
   - *Defect:* a system rail layer was computed but omitted, so access-drop edges landed
     on empty space.
   - *Rule:* every edge terminates on a rendered, labelled element.
   - *Remedy:* include the System band rails so reads / writes land on a named rail; never
     leave an edge pointing into blank canvas.

## Origin

These defect classes and remedies were established by direct human visual review of the
first two full factory renders (a current-state and a future-state Workflow), across
several iteration passes at the human visual gate. Both renders reached the visual
standard once every item above was satisfied. Treat the list as the codified result of
that review, extended as new defect classes are found.
