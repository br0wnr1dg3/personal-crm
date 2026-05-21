-- Sales people at Series A/B/C companies in the 51-500 employee band.
SELECT
  first_name, last_name, company_name, raw_position,
  seniority, industry, employee_band, funding_stage,
  hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Sales'
  AND funding_stage IN ('Series A', 'Series B', 'Series C+')
  AND employee_band IN ('51-200', '201-500')
ORDER BY company_name, seniority;
