# Diagrams

Five pictures of what's built, as of 20/11/2026. Each one has a self-contained `.html`
file (the source: open it in a browser, it works offline) and a light and dark `.png`
rendered from it.

| Diagram | Shows | Used in |
|---|---|---|
| [01-platform](01-platform.png) | The `dev` environment on AWS: the request path, the VPC with private and public subnets, the NAT instance, what's shared per environment and what's per customer, how an alarm reaches the incident loop | [README](../../README.md) |
| [02-incident-loop](02-incident-loop.png) | Alarm, evidence, triage and verify, the GitHub issue, `/approve`, the one action, the recovery check, the post-incident review | [README](../../README.md) |
| [03-ci-trust](03-ci-trust.png) | Each workflow, the OIDC role it gets, and the one thing that role can do; the workflows with no role | [ADR 0009](../adr/0009-oidc-immutable-subject.md) |
| [04-change-lanes](04-change-lanes.png) | The three change lanes: runtime config, the code canary, Terraform for the changed stacks only | [ADR 0004](../adr/0004-three-change-lanes.md) |
| [05-review-agents](05-review-agents.png) | The PR review agents: three jobs at three trust levels, the verifier, the gate and the `accept-risk` override | [ADR 0013](../adr/0013-review-agents.md) |

The numbers in them come from dated runs recorded in [docs/demo](../demo): fix live
312 s after the alarm (S4, issue #19, 19/11/2026), config live in 6-8 s (S1 and S4),
canary promoted after 320-340 s and S2 rolled back in 154 s (19/11/2026).

## Changing one

The HTML is the source of truth: node positions, labels and arrows are plain markup
in it. Edit the HTML, then re-render the PNGs with headless Chromium (Playwright),
screenshotting the `.canvas` element at 2x in the light theme and with
`data-theme="dark"` on `<html>`. When the system changes, update the diagram in the
same PR, the same way as a README number.

## Credits

Made with [stack-diagram](https://github.com/caru-ini/stack-diagram) (MIT). Icons: AWS
Architecture Icons (AWS allows them in architecture diagrams), svgl, devicon, CNCF
Artwork and Lucide; brand logos remain the trademarks of their owners. Fonts embedded in
the HTML: Geist and JetBrains Mono, both under the SIL Open Font License 1.1.
