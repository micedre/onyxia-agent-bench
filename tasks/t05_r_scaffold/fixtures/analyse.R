donnees <- read.csv("donnees_insee.csv")
resume <- aggregate(revenu_disponible ~ departement, data = donnees, FUN = median)
print(resume)
