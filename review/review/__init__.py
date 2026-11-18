"""PR review agents (tier 2, Pillar 4): router -> specialist -> verifier -> one comment.

Standard library only, on purpose: the job that holds the OIDC token (Bedrock invoke on
one model) installs nothing. Model calls go through the runner's preinstalled AWS CLI.
"""
