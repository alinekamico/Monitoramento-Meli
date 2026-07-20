#!/bin/bash
set -e

echo ==> Baixando atualizações do GitHub...
git pull origin develop

echo ==> Reiniciando servidor Flask...
sudo systemctl restart buybox-server

echo ==> Reiniciando scheduler...
sudo systemctl restart buybox-scheduler

echo ""
echo "Deploy concluído! Acesse: http://137.131.134.197/monitoramentomeli"
