# Mudancas so de texto

Mudancas so de texto (rotulos, textos de telas, mensagens, textos de regras e ajuda) que nao alteram fluxo, regra de negocio, dados ou configuracao: rode apenas o lint e os testes dos arquivos e telas afetados, nao a suite completa nem os testes de navegador (e2e). O CI do PR segue o modo leve descrito em .github/workflows/ci.yml. Qualquer mudanca de logica, modelo, migration, JS, CSS estrutural, dependencias, Docker ou do proprio CI e mudanca de processo e roda tudo.
