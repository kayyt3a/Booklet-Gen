#!/usr/bin/env bash
# Ingest the 10 seeded workbooks with proper subject/year/topic tags.
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-.}"

run() {
  echo "----- $1 -----"
  .venv/bin/python -m booklet_gen.rag.ingest "$@"
}

# Year 3 Maths
run rag_sources/f7577b4f-Y3T2W8_Math.pdf              --subject Mathematics --year "Year 3" --topics "Fractions,Word Problems,Operations" --source-name "Y3T2W8_Math_workbook"
run rag_sources/c0d740fa-Y3T2W9_Math_Test50min.pdf    --subject Mathematics --year "Year 3" --topics "End-of-term Test,Fractions,Geometry,Time" --source-name "Y3T2W9_Math_test"

# Year 4 Maths
run rag_sources/8db9c231-Y4T2W7_Math.pdf              --subject Mathematics --year "Year 4" --topics "Fractions,Perimeter,KCF"              --source-name "Y4T2W7_Math_workbook"
run rag_sources/e3258ea2-Y4T2W7_Math_ANS.pdf          --subject Mathematics --year "Year 4" --topics "Fractions,Perimeter,KCF,Answers"       --source-name "Y4T2W7_Math_answers"

# Year 4 English
run rag_sources/9aae0db3-Y4T2W7_Eng.pdf               --subject English --year "Year 4" --topics "Writing,Sensational Starts,Reading Comprehension"           --source-name "Y4T2W7_Eng_workbook"
run rag_sources/9ff0101f-Y4T2W7_Eng_ANS.pdf           --subject English --year "Year 4" --topics "Writing,Sensational Starts,Reading Comprehension,Answers"   --source-name "Y4T2W7_Eng_answers"
run rag_sources/234a3ea5-Y4T2W8_Eng.pdf               --subject English --year "Year 4" --topics "Writing Towards Climax,Reading Comprehension"                --source-name "Y4T2W8_Eng_workbook"
run rag_sources/83923e39-Y4T2W9_Eng_Test_35min.pdf    --subject English --year "Year 4" --topics "End-of-term Test,Language Conventions,Reading"               --source-name "Y4T2W9_Eng_test"

# Year 6 English
run rag_sources/3fca981c-Y6T2W7_English.pdf           --subject English --year "Year 6" --topics "Persuasive Writing,Reading Comprehension"          --source-name "Y6T2W7_Eng_workbook"
run rag_sources/ecff4a4f-Y6T2W7_English_ANS.pdf       --subject English --year "Year 6" --topics "Persuasive Writing,Reading Comprehension,Answers" --source-name "Y6T2W7_Eng_answers"

echo
echo "===== FINAL STORE STATE ====="
.venv/bin/python -c "from booklet_gen.rag.store import VectorStore; s=VectorStore(); print(f'Total chunks in store: {s.count()}')"
