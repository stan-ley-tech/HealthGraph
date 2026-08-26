"""Seed the database with realistic demo data for local development.

Idempotent in the sense that matters for a dev workflow: if any patient
already exists, it does nothing rather than risk duplicate-key errors on
a second run.

Usage:
    python -m scripts.seed
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from faker import Faker
from sqlalchemy import func, select

from app.auth.passwords import hash_password
from app.database.session import AsyncSessionLocal
from app.models.enums import (
    AbnormalFlag,
    AllergySeverity,
    EncounterType,
    FacilityType,
    RelationshipType,
    Role,
    Sex,
    VitalSignType,
)
from app.models.facility import Facility
from app.models.patient import Patient
from app.models.provider import Provider
from app.repositories.clinical import (
    AllergyRepository,
    DiagnosisRepository,
    EncounterRepository,
    MedicationRepository,
    ProcedureRepository,
)
from app.repositories.observations import LabResultRepository, VitalSignRepository
from app.repositories.patient import PatientRepository
from app.repositories.provider import FacilityRepository, ProviderRepository
from app.repositories.relationship import PatientProviderRelationshipRepository
from app.repositories.user import UserRepository

fake = Faker()
Faker.seed(1337)
random.seed(1337)

_DEMO_PASSWORD = "ChangeMe123!"

_CHIEF_COMPLAINTS = [
    "Annual physical",
    "Persistent cough",
    "Follow-up on blood pressure",
    "Lower back pain",
    "Routine diabetes check",
    "Seasonal allergy symptoms",
]

_DIAGNOSES = [
    ("I10", "Essential (primary) hypertension"),
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("M54.5", "Low back pain"),
    ("E78.5", "Hyperlipidemia, unspecified"),
    ("J45.909", "Unspecified asthma, uncomplicated"),
]

_MEDICATIONS = [
    ("Lisinopril", "10mg", "once daily", "oral"),
    ("Metformin", "500mg", "twice daily", "oral"),
    ("Atorvastatin", "20mg", "nightly", "oral"),
    ("Albuterol", "90mcg", "as needed", "inhaled"),
]

_LAB_TESTS = [
    ("Hemoglobin A1c", "4548-4", "%"),
    ("Basic Metabolic Panel - Glucose", "2345-7", "mg/dL"),
    ("Total Cholesterol", "2093-3", "mg/dL"),
    ("TSH", "3016-3", "mIU/L"),
]

_ALLERGENS = [
    ("Penicillin", "Hives", AllergySeverity.MODERATE),
    ("Peanuts", "Anaphylaxis", AllergySeverity.SEVERE),
    ("Latex", "Contact dermatitis", AllergySeverity.MILD),
]

_PROCEDURES = [
    ("99213", "Office visit, established patient"),
    ("93000", "Electrocardiogram, routine"),
    ("80053", "Comprehensive metabolic panel"),
]


async def _already_seeded(session) -> bool:
    result = await session.execute(select(func.count()).select_from(Patient))
    return (result.scalar_one() or 0) > 0


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        if await _already_seeded(session):
            print("Database already has patients — skipping seed.")
            return

        users = UserRepository(session)
        facilities = FacilityRepository(session)
        providers = ProviderRepository(session)
        patients = PatientRepository(session)
        relationships = PatientProviderRelationshipRepository(session)
        encounters = EncounterRepository(session)
        diagnoses = DiagnosisRepository(session)
        medications = MedicationRepository(session)
        allergies = AllergyRepository(session)
        procedures = ProcedureRepository(session)
        lab_results = LabResultRepository(session)
        vital_signs = VitalSignRepository(session)

        users.create(
            email="admin@healthgraph.dev",
            hashed_password=hash_password(_DEMO_PASSWORD),
            role=Role.ADMIN,
        )

        hospital = facilities.add(
            Facility(name="Riverside General Hospital", facility_type=FacilityType.HOSPITAL,
                      address="100 Riverside Dr, Springfield", phone="555-0100")
        )
        clinic = facilities.add(
            Facility(name="Downtown Family Clinic", facility_type=FacilityType.CLINIC,
                      address="42 Main St, Springfield", phone="555-0142")
        )
        lab = facilities.add(
            Facility(name="Metro Diagnostics Lab", facility_type=FacilityType.LAB,
                      address="7 Industrial Way, Springfield", phone="555-0107")
        )
        await session.flush()

        def make_provider(first, last, npi, specialty, facility_id, role):
            user = users.create(
                email=f"{first.lower()}.{last.lower()}@healthgraph.dev",
                hashed_password=hash_password(_DEMO_PASSWORD),
                role=role,
            )
            return user, providers.add(
                Provider(
                    user_id=None, facility_id=facility_id, first_name=first,
                    last_name=last, npi_number=npi, specialty=specialty,
                )
            )

        dr_chen_user, dr_chen = make_provider(
            "Sarah", "Chen", "1000000001", "Internal Medicine", hospital.id, Role.DOCTOR
        )
        dr_okafor_user, dr_okafor = make_provider(
            "James", "Okafor", "1000000002", "Cardiology", hospital.id, Role.DOCTOR
        )
        nurse_user, nurse_reyes = make_provider(
            "Nancy", "Reyes", "1000000003", None, clinic.id, Role.NURSE
        )
        lab_tech_user, lab_tech = make_provider(
            "Tom", "Whitfield", "1000000004", None, lab.id, Role.LAB_TECHNICIAN
        )
        await session.flush()
        for user, provider in (
            (dr_chen_user, dr_chen),
            (dr_okafor_user, dr_okafor),
            (nurse_user, nurse_reyes),
            (lab_tech_user, lab_tech),
        ):
            provider.user_id = user.id

        doctors = [dr_chen, dr_okafor]
        today = datetime.now(UTC)

        for i in range(1, 9):
            sex = random.choice([Sex.MALE, Sex.FEMALE])
            first_name = fake.first_name_male() if sex is Sex.MALE else fake.first_name_female()
            last_name = fake.last_name()
            dob = fake.date_of_birth(minimum_age=22, maximum_age=85)

            patient_user = None
            if i <= 3:
                patient_user = users.create(
                    email=f"{first_name.lower()}.{last_name.lower()}@patient.healthgraph.dev",
                    hashed_password=hash_password(_DEMO_PASSWORD),
                    role=Role.PATIENT,
                )
                await session.flush()

            patient = patients.create(
                mrn=f"MRN-{1000 + i}",
                first_name=first_name,
                last_name=last_name,
                date_of_birth=dob,
                sex=sex,
                phone=fake.phone_number()[:20],
                email=fake.email(),
                address=fake.address().replace("\n", ", "),
                ssn=fake.ssn(),
            )
            await session.flush()
            if patient_user is not None:
                patient.user_id = patient_user.id

            primary_doctor = random.choice(doctors)
            relationships.create(
                patient_id=patient.id,
                provider_id=primary_doctor.id,
                relationship_type=RelationshipType.PRIMARY_CARE,
                start_date=dob + timedelta(days=365 * random.randint(20, 60)),
            )
            if random.random() < 0.4:
                relationships.create(
                    patient_id=patient.id,
                    provider_id=nurse_reyes.id,
                    relationship_type=RelationshipType.CONSULTING,
                    start_date=today.date() - timedelta(days=random.randint(30, 400)),
                )

            num_encounters = random.randint(1, 3)
            for _ in range(num_encounters):
                days_ago = random.randint(5, 500)
                hours = random.randint(0, 12)
                started_at = today - timedelta(days=days_ago, hours=hours)
                encounter_type = random.choice(
                    [EncounterType.OUTPATIENT, EncounterType.OUTPATIENT, EncounterType.TELEHEALTH]
                )
                encounter = encounters.create(
                    patient_id=patient.id,
                    provider_id=primary_doctor.id,
                    facility_id=hospital.id if random.random() < 0.7 else clinic.id,
                    encounter_type=encounter_type,
                    chief_complaint=random.choice(_CHIEF_COMPLAINTS),
                    started_at=started_at,
                )
                await session.flush()

                icd10, description = random.choice(_DIAGNOSES)
                diagnoses.create(
                    patient_id=patient.id,
                    encounter_id=encounter.id,
                    icd10_code=icd10,
                    description=description,
                    diagnosed_at=started_at,
                )
                vital_signs.create(
                    patient_id=patient.id,
                    encounter_id=encounter.id,
                    vital_type=VitalSignType.HEART_RATE,
                    value=random.randint(58, 95),
                    unit="bpm",
                    recorded_at=started_at,
                )
                vital_signs.create(
                    patient_id=patient.id,
                    encounter_id=encounter.id,
                    vital_type=VitalSignType.TEMPERATURE,
                    value=round(random.uniform(97.0, 99.5), 1),
                    unit="F",
                    recorded_at=started_at,
                )

                if random.random() < 0.6:
                    name, dosage, frequency, route = random.choice(_MEDICATIONS)
                    medications.create(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        prescribed_by=primary_doctor.id,
                        name=name,
                        dosage=dosage,
                        frequency=frequency,
                        route=route,
                        start_date=started_at.date(),
                    )

                if random.random() < 0.5:
                    test_name, test_code, unit = random.choice(_LAB_TESTS)
                    lab_results.create(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        ordered_by=primary_doctor.id,
                        test_name=test_name,
                        test_code=test_code,
                        value=str(round(random.uniform(4.0, 9.5), 1)),
                        unit=unit,
                        resulted_at=started_at + timedelta(days=1),
                        abnormal_flag=random.choice(
                            [AbnormalFlag.NORMAL, AbnormalFlag.NORMAL, AbnormalFlag.HIGH]
                        ),
                    )

                if random.random() < 0.25:
                    cpt_code, description = random.choice(_PROCEDURES)
                    procedures.create(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        performed_by=primary_doctor.id,
                        cpt_code=cpt_code,
                        description=description,
                        performed_at=started_at,
                    )

            if random.random() < 0.4:
                allergen, reaction, severity = random.choice(_ALLERGENS)
                allergies.create(
                    patient_id=patient.id,
                    allergen=allergen,
                    reaction=reaction,
                    severity=severity,
                    recorded_at=today - timedelta(days=random.randint(100, 1000)),
                )

        await session.commit()
        print("Seed complete. All demo accounts share one password:")
        print(f"  password:       {_DEMO_PASSWORD}")
        print("  admin:          admin@healthgraph.dev")
        print("  doctors:        sarah.chen@healthgraph.dev, james.okafor@healthgraph.dev")
        print("  nurse:          nancy.reyes@healthgraph.dev")
        print("  lab technician: tom.whitfield@healthgraph.dev")
        print("  8 patients seeded; the first 3 also have their own PATIENT-role login.")


if __name__ == "__main__":
    asyncio.run(seed())
