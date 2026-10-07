#!/usr/bin/env bash
# Orders 1.4.0 ships stackable discount codes and throws NullPointerException when no code is given.
# Expected root cause: bad_deploy (orders). Image built by sandbox/releases/build.sh.
source "$(dirname "$0")/lib.sh"
deploy orders 1.4.0 'orders 1.4.0: support stacked discount codes (#212)'
