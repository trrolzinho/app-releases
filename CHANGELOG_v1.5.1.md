**TofuBot v1.5.1**  
**🐛 Correções**  
- **[Crítico]** Corrigido: quem usa o .exe e clica em "Atualizar" ficava com a pasta temporária de extração (_update_tmp_extract) e o script _aplicar_atualizacao_painel.bat **sempre para trás**, mesmo depois da atualização "terminar"  que não deveria sobrar na pasta de quem só usa o .exe. Causa: um ^ sobrando antes do > na linha que apaga essa pasta fazia o Windows tratar o > como texto em vez de redirecionamento, e a falha do apagar era 100% silenciosa (sem aviso nenhum). Corrigido, com até 5 tentativas (a pasta recém-extraída pode ficar brevemente travada por antivírus).  
- Quem já atualizou pra v1.5.0 antes dessa correção: basta clicar em "Atualizar" de novo —  
**🆕 Novidades**  
- **controle.exe** — o Bot de Controle via Telegram agora também vira .exe no build automático do GitHub, igual painel.exe/bot.exe. Quem usa os .exe não precisa mais ter Python instalado pra rodar o controle: é só dar 2 cliques em controle.exe   
*Versão anterior publicada: v1.5.0.*  
