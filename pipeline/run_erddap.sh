#!/bin/bash
cd /home/user/workspace/socal
echo "START $(date)"
python fetch_mur.py extras
python fetch_mur.py zones 0 9
python fetch_mur.py grid 6
echo ALLDONE
