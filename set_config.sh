#!/bin/bash

export $(cat mailcow.conf | grep -v ^# | xargs)
export COMPOSE_CONVERT_WINDOWS_PATHS=1