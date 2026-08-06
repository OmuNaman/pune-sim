"""V0 name pools, keyed by religion (08-identity §2: names are a structural
derivation of identity — the strongest identity signal in any prompt, so they
must come from governed pools, not model priors). V0 pools are small and
curated; V3 re-keys them to (religion, jati_cluster, mother_tongue, sex,
birth_decade) per the blueprint.
"""

GIVEN: dict[tuple[str, str], list[str]] = {
    ("hindu", "m"): [
        "Aditya", "Omkar", "Prasad", "Ninad", "Sarang", "Makarand", "Vikram", "Sachin",
        "Mahesh", "Dnyaneshwar", "Kedar", "Abhijit", "Rohan", "Shreyas", "Vinayak",
        "Suhas", "Anant", "Bhalchandra", "Sadanand", "Yashwant",
    ],
    ("hindu", "f"): [
        "Ketaki", "Sneha", "Pallavi", "Aarti", "Madhura", "Vaishali", "Sunita", "Asha",
        "Mrunal", "Gauri", "Revati", "Sharvari", "Anagha", "Vasudha", "Manasi",
        "Shubhangi", "Nirmala", "Sulbha", "Vandana", "Ujwala",
    ],
    ("muslim", "m"): [
        "Arman", "Salim", "Farooq", "Imran", "Yusuf", "Iqbal", "Sadiq", "Rafiq",
        "Zubair", "Aamir", "Nadeem", "Shahid", "Akbar", "Hamid", "Javed", "Firoz",
    ],
    ("muslim", "f"): [
        "Ayesha", "Nafisa", "Shabana", "Rukhsana", "Farzana", "Zainab", "Sana",
        "Yasmin", "Heena", "Shaheen", "Nilofar", "Rehana", "Sultana", "Amina",
    ],
    ("buddhist_navayana", "m"): [
        "Siddharth", "Rahul", "Milind", "Prakash", "Anand", "Bhimrao", "Dinesh",
        "Sunil", "Vijay", "Pradeep", "Ashok", "Ramesh",
    ],
    ("buddhist_navayana", "f"): [
        "Sujata", "Kavita", "Meera", "Savita", "Pradnya", "Sheela", "Rajani",
        "Lata", "Usha", "Chhaya", "Mangala", "Kalpana",
    ],
    ("jain", "m"): [
        "Paras", "Nirav", "Rishabh", "Mahavir", "Sanjay", "Rajesh", "Nitin",
        "Bharat", "Kirti", "Mukesh", "Padam", "Vimal",
    ],
    ("jain", "f"): [
        "Pooja", "Khushi", "Sonal", "Meena", "Rekha", "Sarita", "Nisha",
        "Priti", "Jaya", "Kiran", "Manju", "Sushila",
    ],
    ("christian", "m"): [
        "Joseph", "Melwyn", "Savio", "Alex", "Sunil", "Franklin", "Ivan",
        "Clement", "Rocky", "Vincent", "Patrick", "Neil",
    ],
    ("christian", "f"): [
        "Maria", "Priscilla", "Sharon", "Anita", "Gloria", "Celine", "Rosy",
        "Jennifer", "Lydia", "Veronica", "Agnes", "Clara",
    ],
}

SURNAME: dict[str, list[str]] = {
    "hindu": [
        "Deshpande", "Kulkarni", "Joshi", "Jagtap", "Pawar", "Shinde", "More",
        "Bhosale", "Chavan", "Salunkhe", "Kale", "Ranade", "Gokhale", "Apte",
        "Damle", "Shelar", "Mane", "Thorat", "Ghorpade", "Phadke", "Sathe", "Marne",
    ],
    "muslim": [
        "Shaikh", "Sayyed", "Pathan", "Khan", "Ansari", "Qureshi", "Attar",
        "Mulla", "Tamboli", "Bagwan", "Inamdar", "Momin", "Maniyar", "Shikalgar",
    ],
    "buddhist_navayana": [
        "Kamble", "Sonawane", "Waghmare", "Khandare", "Gaikwad", "Jadhav",
        "Ovhal", "Salve", "Bansode", "Kharat", "Bhalerao", "Ahire",
    ],
    "jain": [
        "Shah", "Oswal", "Bora", "Lodha", "Mehta", "Gandhi", "Kothari",
        "Bafna", "Chhajed", "Ranka", "Munot", "Dugad",
    ],
    "christian": [
        "D'Souza", "Fernandes", "Pereira", "Gonsalves", "Lobo", "Rodrigues",
        "Noronha", "Mascarenhas", "Pinto", "Almeida",
    ],
}
