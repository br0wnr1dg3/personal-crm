-- Senior marketing leaders worth knowing.
SELECT
  first_name, last_name, company_name, raw_position,
  seniority, industry, employee_band, hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Marketing'
  AND seniority IN ('VP', 'C-suite')
ORDER BY industry, company_name;
