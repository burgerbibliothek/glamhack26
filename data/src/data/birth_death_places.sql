/* Aggregate birth / death place of persons */
SELECT pd.id as place_id, pd2."label" AS person_label, pd2.ark, role FROM  main.places_descriptors pd 
LEFT JOIN main.persons_descriptors_places_descriptors pdpd ON pd.id = pdpd.id_place_descriptor 
LEFT JOIN main.persons_descriptors pd2 ON pd2.id = pdpd.id_person_descriptor 
WHERE pdpd."role" = 1 OR pdpd."role" = 2
ORDER BY pd2.label