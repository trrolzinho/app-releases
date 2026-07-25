#!/bin/bash
cd "$(dirname "$0")"
./venv/bin/python3 telegram_controle.py
echo ""
read -p "Pressione Enter para fechar..."
