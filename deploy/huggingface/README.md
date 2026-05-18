---
title: Concord
emoji: 🛡
colorFrom: gray
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Production-shaped multi-agent customer support with governed actions.
---

# Concord

Production-shaped multi-agent customer support system. Verified identity, scoped
retrieval, governed actions, independent verification, full audit trail.

This Space deploys the FastAPI backend and the demo UI in one container.

**Source:** https://github.com/saikumardeepak1/concord

## What to try

Once the Space boots (first build takes 3-4 minutes while it installs
sentence-transformers and Chroma):

1. Pick a customer from the right panel (six fixtures, each engineered to
   trigger a different safety gate).
2. Click one of the 17 scripted scenarios on the left, or type your own.
3. Read the live trace on the right to see which gate decided the outcome.

## Configuration

This Space needs `ANTHROPIC_API_KEY` set as a Space secret
(`Settings → Variables and secrets → New secret`).

## Architecture

See the GitHub repo for the full walkthrough, ADRs, and the 153-case eval
suite.
