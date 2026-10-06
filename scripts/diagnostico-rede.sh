#!/usr/bin/env bash
# Diagnóstico de rede entre as máquinas da coleta (ex.: Mac mini <-> Mac principal).
# Diz qual roteador esta máquina usa, se alcança o roteador e a outra máquina, e o estado do
# firewall. Só lê; não muda nenhuma configuração. Compatível com o bash 3.2 do macOS.
#
#   scripts/diagnostico-rede.sh [IP_DA_OUTRA_MAQUINA]     (padrão: 192.168.0.78)
set -u
OUTRA="${1:-192.168.0.78}"

echo "== esta máquina: $(scutil --get ComputerName 2>/dev/null || hostname)"
echo "== rota padrão"
route -n get default 2>/dev/null | grep -E "gateway|interface"
GW=$(route -n get default 2>/dev/null | awk '/gateway/{print $2}')

echo "== interfaces com endereço"
for i in $(ifconfig -l); do
  ip=$(ifconfig "$i" 2>/dev/null | awk '/inet /{print $2}' | grep -v '^127\.' | head -1)
  [ -n "$ip" ] && echo "$i  $ip  $(ifconfig "$i" | awk '/ether/{print $2}')"
done

echo "== wi-fi"
for i in en0 en1 en2; do
  r=$(networksetup -getairportnetwork "$i" 2>/dev/null)
  case "$r" in *"Current Wi-Fi Network"*) echo "$i: $r" ;; esac
done

echo "== roteador ($GW)"
ping -c 3 -t 5 "$GW" 2>/dev/null | tail -1
arp -n "$GW" 2>/dev/null

echo "== outra máquina ($OUTRA)"
ping -c 3 -t 5 "$OUTRA" 2>/dev/null | tail -1
arp -n "$OUTRA" 2>/dev/null
if nc -z -G 3 "$OUTRA" 8765 2>/dev/null; then echo "porta 8765 (TSE falso do ensaio): aberta"; else echo "porta 8765: sem resposta (normal se o ensaio não estiver rodando)"; fi

echo "== firewall"
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate --getstealthmode 2>/dev/null
