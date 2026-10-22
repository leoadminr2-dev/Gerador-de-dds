# Padrao aprovado: quadro de responsaveis do DDS

**Status:** padrao visual aprovado em 22/10/2026.

O preenchimento institucional deve seguir a MESMA formatacao em todos os turnos e unidades:
- **Numero**: `01` / `02`, centralizado no quadradinho estreito a esquerda da celula do nome; **sem ponto** apos o numero.
- **Nome**: somente o nome do responsavel na celula maior, com letras discretamente espacadas (`0.35 pt`), Helvetica 7 pt.
- **Sem fundo**: nao inserir retangulos brancos, mascara ou preenchimento atras do numero e do nome.
- **Bordas preservadas**: nunca redesenhar ou interromper as linhas do modelo original.
- **Linhas verticais**: manter sempre os dois espacos reservados no formulario. Com um responsavel, usar somente a primeira linha; deixar a segunda sem numero/nome.
- **Alturas aprovadas**: primeira linha na altura aprovada para ORLANDO BARBOSA; segunda linha na aprovada para EVELYN MONTOVANI.
- **Outras unidades**: aplicar as mesmas posicoes aos responsaveis especificos de TECA, UNITED e GATE GOURMET.

Responsaveis atuais:
- Diurno (GIG): Orlando Barbosa / Evelyn Montovani
- Vespertino (GIG): Kayo Lessa / Bruna Fraga
- Noturno (GIG): Maria do Socorro
- TECA: Pedro Paulo
- UNITED: Wilker Maria
- GATE GOURMET: Marcia Fasutino

Referencia tecnica: `RESPONSIBLE_NUMBER_CELL`, `RESPONSIBLE_NAME_LEFT_INSET`,
`RESPONSIBLE_LETTER_SPACING` e `RESPONSIBLE_ROW_ADJUST_Y` em `app.py`.

Validacao: `tests/test_build_pdf_batched.py`, cobrindo todos os turnos e unidades.
Alteracoes futuras no quadro de responsaveis devem manter esse padrao.
