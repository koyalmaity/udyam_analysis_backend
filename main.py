from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(
    title="Udyam360 Competitor Analysis API",
    description="Same-category hyper-local competitor analysis",
    version="1.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# PostgreSQL connection


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

engine = create_engine(DATABASE_URL)



@app.get("/")
def home():
    return {
        "message": "Udyam360 Competitor Analysis API is running"
    }


@app.get("/competitors")
def get_competitors(
    lat: float,
    lon: float,
    category: str,
    radius_km: float = Query(default=1.0, gt=0, le=50)
):

    radius_m = radius_km * 1000

    with engine.connect() as connection:

        result = connection.execute(
            text("""
                SELECT
                    business_name,
                    business_group,
                    confidence,
                    ROUND(
                        ST_Distance(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography
                        )::numeric,
                        2
                    ) AS distance_m

                FROM competitors

                WHERE business_group = :category

                  AND ST_DWithin(
                      geom::geography,
                      ST_SetSRID(
                          ST_MakePoint(:lon, :lat),
                          4326
                      )::geography,
                      :radius_m
                  )

                ORDER BY distance_m;
            """),
            {
                "lat": lat,
                "lon": lon,
                "category": category,
                "radius_m": radius_m
            }
        )

        rows = result.fetchall()

    competitors = [
        {
            "business_name": row.business_name,
            "category": row.business_group,
            "confidence": row.confidence,
            "distance_m": float(row.distance_m)
        }
        for row in rows
    ]

    return {
        "category": category,
        "radius_km": radius_km,
        "competitor_count": len(competitors),
        "competitors": competitors
    }

@app.get("/density")
def get_density(
    lat: float,
    lon: float,
    category: str,
    radius_km: float = Query(default=1.0, gt=0, le=50)
):
    radius_m = radius_km * 1000

    with engine.connect() as connection:
        result = connection.execute(
            text("""
                SELECT COUNT(*) AS competitor_count
                FROM competitors
                WHERE business_group = :category
                  AND ST_DWithin(
                      geom::geography,
                      ST_SetSRID(
                          ST_MakePoint(:lon, :lat),
                          4326
                      )::geography,
                      :radius_m
                  );
            """),
            {
                "lat": lat,
                "lon": lon,
                "category": category,
                "radius_m": radius_m
            }
        )

        competitor_count = result.scalar()

    # Simple density classification
    if competitor_count == 0:
        density_level = "No Competition"
    elif competitor_count <= 5:
        density_level = "Low"
    elif competitor_count <= 15:
        density_level = "Medium"
    else:
        density_level = "High"

    return {
        "category": category,
        "radius_km": radius_km,
        "competitor_count": competitor_count,
        "density_level": density_level
    }

@app.get("/analysis")
def get_analysis(
    lat: float,
    lon: float,
    category: str
):
    with engine.connect() as connection:

        # Count competitors at different radii
        result = connection.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE ST_DWithin(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography,
                            1000
                        )
                    ) AS within_1km,

                    COUNT(*) FILTER (
                        WHERE ST_DWithin(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography,
                            3000
                        )
                    ) AS within_3km,

                    COUNT(*) FILTER (
                        WHERE ST_DWithin(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography,
                            5000
                        )
                    ) AS within_5km

                FROM competitors
                WHERE business_group = :category;
            """),
            {
                "lat": lat,
                "lon": lon,
                "category": category
            }
        )

        counts = result.fetchone()

        # Find nearest same-category competitor
        nearest_result = connection.execute(
            text("""
                SELECT
                    business_name,
                    ROUND(
                        ST_Distance(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography
                        )::numeric,
                        2
                    ) AS distance_m

                FROM competitors

                WHERE business_group = :category

                ORDER BY geom <-> ST_SetSRID(
                    ST_MakePoint(:lon, :lat),
                    4326
                )

                LIMIT 1;
            """),
            {
                "lat": lat,
                "lon": lon,
                "category": category
            }
        )

        nearest = nearest_result.fetchone()

    # Classify competition based on 1 km count
    if counts.within_1km == 0:
        competition_level = "No Competition"
    elif counts.within_1km <= 5:
        competition_level = "Low"
    elif counts.within_1km <= 15:
        competition_level = "Medium"
    else:
        competition_level = "High"

    return {
        "category": category,
        "location": {
            "lat": lat,
            "lon": lon
        },
        "competitors": {
            "within_1km": counts.within_1km,
            "within_3km": counts.within_3km,
            "within_5km": counts.within_5km
        },
        "nearest_competitor": {
            "business_name": nearest.business_name if nearest else None,
            "distance_m": float(nearest.distance_m) if nearest else None
        },
        "competition_level": competition_level
    }

@app.get("/density-grid")
def get_density_grid(
    category: str
):
    with engine.connect() as connection:

        result = connection.execute(
            text("""
                SELECT
                    ST_XMin(cell) AS min_lon,
                    ST_YMin(cell) AS min_lat,
                    ST_XMax(cell) AS max_lon,
                    ST_YMax(cell) AS max_lat,
                    COUNT(c.id) AS competitor_count

                FROM
                    ST_SquareGrid(
                        0.001,
                        ST_MakeEnvelope(
                            78.256,
                            21.445,
                            78.310,
                            21.484,
                            4326
                        )
                    ) AS grid(cell)

                LEFT JOIN competitors c
                ON ST_Within(c.geom, grid.cell)
                AND c.business_group = :category

                GROUP BY cell
                ORDER BY competitor_count DESC;
            """),
            {
                "category": category
            }
        )

        rows = result.fetchall()

    cells = []

    for row in rows:

        count = int(row.competitor_count)

        if count == 0:
            density_level = "No Competition"
        elif count == 1:
            density_level = "Low"
        elif count <= 3:
            density_level = "Medium"
        else:
            density_level = "High"

        cells.append({
            "min_lat": float(row.min_lat),
            "min_lon": float(row.min_lon),
            "max_lat": float(row.max_lat),
            "max_lon": float(row.max_lon),
            "competitor_count": count,
            "density_level": density_level
        })

    return {
        "category": category,
        "grid_size": "0.001 degree",
        "cells": cells
    }

@app.get("/map-data")
def get_map_data(
    lat: float,
    lon: float,
    category: str,
    radius_km: float = Query(default=1.0, gt=0, le=50)
):
    radius_m = radius_km * 1000

    with engine.connect() as connection:

        result = connection.execute(
            text("""
                SELECT
                    business_name,
                    business_group,
                    confidence,
                    lat,
                    lon,

                    ROUND(
                        ST_Distance(
                            geom::geography,
                            ST_SetSRID(
                                ST_MakePoint(:lon, :lat),
                                4326
                            )::geography
                        )::numeric,
                        2
                    ) AS distance_m

                FROM competitors

                WHERE business_group = :category

                  AND ST_DWithin(
                      geom::geography,
                      ST_SetSRID(
                          ST_MakePoint(:lon, :lat),
                          4326
                      )::geography,
                      :radius_m
                  )

                ORDER BY distance_m;
            """),
            {
                "lat": lat,
                "lon": lon,
                "category": category,
                "radius_m": radius_m
            }
        )

        rows = result.fetchall()

    competitor_locations = []

    for row in rows:
        competitor_locations.append({
            "business_name": row.business_name,
            "category": row.business_group,
            "confidence": row.confidence,
            "lat": float(row.lat),
            "lon": float(row.lon),
            "distance_m": float(row.distance_m)
        })

    return {
        "proposed_location": {
            "lat": lat,
            "lon": lon
        },
        "category": category,
        "radius_km": radius_km,
        "competitor_count": len(competitor_locations),
        "competitors": competitor_locations
    }