-- Edit the industry list to match your current business focus.
SELECT
  first_name, last_name, company_name, raw_position,
  industry, employee_band, funding_stage, hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Founder'
  AND industry IN ('Software', 'Technology', 'AI Tooling')
ORDER BY company_name;
